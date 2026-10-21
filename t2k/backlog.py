"""대기 중인 동영상을 한 번에 하나씩 요약한다 (파이프라인이 process 를 호출한다).

YouTube 에 예의를 지키는 것이 우선이다: 자막 요청 사이에 MIN_GAP_SECONDS 이상 쉬고, blocked 이면 전체를 즉시 멈춘다.

건너뛰는 동영상: summaries/ 아래에 `_<video_id>.md` 가 있는 것, state/failed.jsonl 에 있는 것.
실패 처리:
- no_transcript · unavailable · invalid · 요약 형식 오류 → failed.jsonl 에 기록하고 다음 동영상으로 간다.
- transient → state/attempts.json 의 횟수를 늘리고 다음으로 간다. max_attempts 회가 되면 failed.jsonl 로 옮긴다.
- blocked → 재시도하지 않고 전체를 멈춘다.
- claude -p 의 일시 오류 → 전체를 멈춘다. 계속하면 요약하지 못할 자막을 YouTube 에 계속 요청하게 되기 때문이다.
  다음 주기에는 지난번에 받은 자막(transcripts/<video_id>.json, summarize 가 claude 호출 전에 저장한다)을 다시 쓰고
  YouTube 에 다시 요청하지 않는다.
- yt-dlp 를 실행하지 못함(tool) → 아무 것도 기록하지 않고 전체를 멈춘다. 동영상의 문제가 아니라 설치 문제다.
"""
import json
import time
from datetime import datetime
from functools import partial
from pathlib import Path

from . import config, summarize, transcript
from .summarize import now as local_now, SummaryError

MIN_GAP_SECONDS = 20


def _now_iso() -> str:
    return local_now().isoformat(timespec="seconds")


class State:
    """state/failed.jsonl (영구 실패) 과 state/attempts.json (동영상별 transient 실패 횟수)."""

    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.failed_file = state_dir / "failed.jsonl"
        self.attempts_file = state_dir / "attempts.json"
        self.removed_file = state_dir / "removed.jsonl"

    def failed_ids(self) -> set[str]:
        if not self.failed_file.exists():
            return set()
        return {json.loads(line)["video_id"] for line in self.failed_file.read_text().splitlines() if line.strip()}

    def removed_ids(self) -> set[str]:
        """삭제한 요약 (t2k remove · 사이트의 삭제 버튼). 다시 요약하지 않는다."""
        if not self.removed_file.exists():
            return set()
        return {json.loads(line)["video_id"] for line in self.removed_file.read_text().splitlines() if line.strip()}

    def fail(self, it: dict, reason: str, error: str) -> None:
        rec = {"video_id": it["video_id"], "title": it.get("title", ""), "reason": reason, "error": error,
               "time": _now_iso()}
        with self.failed_file.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.clear_attempts(it["video_id"])

    def attempts(self) -> dict[str, int]:
        return json.loads(self.attempts_file.read_text()) if self.attempts_file.exists() else {}

    def add_attempt(self, vid: str) -> int:
        a = self.attempts()
        a[vid] = a.get(vid, 0) + 1
        self._write_attempts(a)
        return a[vid]

    def clear_attempts(self, vid: str) -> None:
        a = self.attempts()
        if a.pop(vid, None) is not None:
            self._write_attempts(a)

    def _write_attempts(self, a: dict[str, int]) -> None:
        self.attempts_file.write_text(json.dumps(a, ensure_ascii=False, indent=1))


def summarized_names(summaries_dir: Path) -> list[str]:
    return [p.name for p in summaries_dir.glob("*/*.md")]


def pending(items: list[dict], summaries_dir: Path, state: State) -> list[dict]:
    names, failed = summarized_names(summaries_dir), state.failed_ids() | state.removed_ids()
    return [it for it in items
            if it["video_id"] not in failed and not any(n.endswith(f"_{it['video_id']}.md") for n in names)]


def cached_transcript(vid: str) -> dict | None:
    """지난 시도에서 저장한 자막 (요약 전에 claude 가 실패한 경우). 없거나 읽을 수 없으면 None."""
    f = config.path("transcripts") / f"{vid}.json"
    try:
        tr = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    return tr if tr.get("status") == "ok" and tr.get("video_id") == vid else None


def process(it: dict, state: State, max_attempts: int, fetch=transcript.fetch,
            summarize_fn=summarize.summarize, on_fetched=lambda: None) -> tuple[str, str]:
    """동영상 1편을 처리하고 (결과, 설명) 을 반환한다.
    결과: done (요약 저장) · failed (failed.jsonl 에 기록) · transient (일시 실패, 횟수 증가) ·
    blocked (YouTube 차단, 아무 것도 기록하지 않음) · claude (claude -p 오류, 아무 것도 기록하지 않음) ·
    tool (yt-dlp 를 실행하지 못함, 아무 것도 기록하지 않음)."""
    vid = it["video_id"]
    tr = cached_transcript(vid)
    if tr is None:
        tr = fetch(vid)
        on_fetched()  # 요청 간격은 자막 요청이 끝난 시각부터 잰다
    if tr["status"] != "ok":
        reason, err = tr.get("reason", "transient"), tr.get("error", "")
        if reason == "blocked":
            return "blocked", f"중지: YouTube 차단 (blocked) — {err}"
        if reason == "tool":
            return "tool", f"중지: {err}"
        if reason == "transient":
            n = state.add_attempt(vid)
            if n >= max_attempts:
                state.fail(it, reason, f"{n}회 실패: {err}")
                return "failed", f"실패 기록: transient {n}회 — {err}"
            return "transient", f"일시 실패 {n}/{max_attempts}회 — {err}"
        state.fail(it, reason, err)
        return "failed", f"실패 기록: {reason} — {err}"
    try:
        _, s = summarize_fn(tr)
    except SummaryError as e:
        if e.reason != "format":
            return "claude", f"중지: claude -p 오류 — {e}"
        state.fail(it, "format", str(e))
        return "failed", f"실패 기록: format — {e}"
    state.clear_attempts(vid)
    return "done", f"{s['category']} {s['rating']}/5"


def run(items: list[dict], limit: int | None = None, *, summaries_dir: Path | None = None,
        state_dir: Path | None = None, fetch=transcript.fetch, summarize_fn=summarize.summarize,
        clock=time.monotonic, sleep=time.sleep, out=partial(print, flush=True)) -> dict:
    """대기 중인 동영상을 차례로 요약하고 집계를 반환한다. stopped 는 멈춘 사유(blocked · claude · tool) 또는 None."""
    summaries_dir = summaries_dir or config.path("summaries")
    state = State(state_dir or config.path("state"))
    max_attempts = config.settings()["queue"]["max_attempts"]
    todo = pending(items, summaries_dir, state)
    if limit is not None:
        todo = todo[:limit]
    stats = {"queued": len(todo), "done": 0, "failed": 0, "transient": 0, "stopped": None}
    out(f"{_now_iso()} 대기 {len(todo)}개 (전체 {len(items)}개 중)")
    last = None
    for it in todo:
        vid = it["video_id"]
        if last is not None:
            wait = MIN_GAP_SECONDS - (clock() - last)
            if wait > 0:
                sleep(wait)
        mark = []
        kind, detail = process(it, state, max_attempts, fetch, summarize_fn, on_fetched=lambda: mark.append(clock()))
        last = mark[0] if mark else clock()
        out(f"{_now_iso()} {vid} {detail}")
        if kind in ("blocked", "claude", "tool"):
            stats["stopped"] = kind
            break
        stats[{"done": "done", "failed": "failed", "transient": "transient"}[kind]] += 1
    out(f"{_now_iso()} 요약 {stats['done']} · 실패 기록 {stats['failed']} · 일시 실패 {stats['transient']}"
        + (f" · 중지 ({stats['stopped']})" if stats["stopped"] else ""))
    return stats
