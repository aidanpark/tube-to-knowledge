"""요약 파이프라인 한 주기. launchd 가 1분마다 실행한다 (`t2k pipeline`, service.py).

1. 잠금을 확보한다. 이전 주기가 아직 실행 중이면 바로 끝낸다.
2. 대기열: 사람(config.toml 의 [[user]])마다 디스코드 채널의 새 메시지를 읽어 그 사람의 state/inbox.jsonl 에 넣는다.
   YouTube Data API 는 호출하지 않는다.
3. 대기 중인 동영상: inbox 중 요약 · 실패 기록 · 삭제가 없는 것. 사람을 번갈아 1편씩 처리한다.
4. 요약: 차단으로 쉬는 중이 아니면 1편씩, 자막 요청 사이 20초 이상 간격으로 처리한다 (한 주기 최대 TIME_BUDGET 초).
   요약 · 카테고리 · 관심사는 그 사람의 것을 쓴다 (config.as_user).
   - 실패하면 backlog.process 의 규칙으로 기록한다.
   - YouTube 차단(blocked)이면 파이프라인 전체를 쉰다: 30분에서 시작해 연속 차단마다 2배, 최대 4시간.
   주간 요약: 매주 일요일 24:00(월요일 0시)이 지나면 지난주의 주간 요약을 한 주기에 한 건씩 만든다 (weekly.py).
   영상 요약이 먼저다: 대기 중인 동영상이 없거나 차단으로 쉬는 중일 때만 만든다.
5. 사이트 조작 반영: 사이트에서 한 조작(요약 삭제 · 이동, 카테고리 추가 · 삭제)의 대기열(Cloudflare KV)을
   SYNC_OPS_EVERY 초마다 확인한다 (site_ops).
6. 데이터 폴더의 요약 · 카테고리를 커밋하고(원격 저장소가 있으면 푸시한다), 바뀐 사람의 사이트를 생성해 그 사람의 Worker 에 올린다 (deploy.py).
   배포한 뒤에 KV 의 대기 항목을 지운다. 배포에 실패한 사람은 다음 주기에 다시 배포한다.
7. 디스코드에 결과를 알린다: 요약했으면 ✅ 와 요약 주소, 실패했으면 ❌ 와 사유로 답장한다.
8. 상태를 state/status.json 에 남긴다 (차단 · 조회 시각은 사람 모두에게 같다). 사이트는 이 파일로
   「자막 조회 차단 중」 안내를 표시한다.
"""
import fcntl
import itertools
import json
import subprocess
import time
from datetime import datetime
from functools import partial
from pathlib import Path

from . import backlog, config, deploy, discord_inbox, site, site_ops, weekly
from .summarize import SummaryError, now as local_now, read_front_matter

TIME_BUDGET = 240           # 한 주기에서 요약에 쓰는 최대 시간(초). 다음 주기는 잠금으로 기다린다
POLL_EVERY = 60             # 디스코드 채널 조회 간격(초)
SYNC_OPS_EVERY = 300        # 조작 대기열 확인 간격(초). KV 목록 조회는 하루 1,000회까지 무료다
PAUSES = [1800, 3600, 7200, 14400]  # 차단 시 쉬는 시간: 30분 → 1시간 → 2시간 → 4시간(최대)
BLOCKED_BANNER_AFTER = 14400        # 차단이 4시간 이상 이어지면 사이트에 안내한다

log = partial(print, flush=True)


def now_iso() -> str:
    return local_now().isoformat(timespec="seconds")


class Status:
    """state/status.json: 쉬는 시각, 차단 시작 시각, 재승인 필요 여부, 마지막 조회 · 삭제 확인 시각."""

    def __init__(self, path: Path):
        self.path = path
        self.d = json.loads(path.read_text()) if path.exists() else {}

    def save(self) -> None:
        self.d["last_run"] = now_iso()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1))
        tmp.replace(self.path)

    def paused(self, now: float) -> bool:
        return now < self.d.get("pause_until", 0)

    def blocked(self, now: float) -> None:
        level = min(self.d.get("pause_level", 0), len(PAUSES) - 1)
        self.d["pause_until"] = now + PAUSES[level]
        self.d["pause_level"] = level + 1
        self.d.setdefault("blocked_since", now)

    def unblocked(self) -> None:
        for k in ("pause_until", "pause_level", "blocked_since"):
            self.d.pop(k, None)


def results(vids: set[str], summaries: Path, st: backlog.State) -> tuple[dict, dict, set]:
    """디스코드에 알릴 결과: (요약한 것 → 앞머리, 실패한 것 → 사유, 삭제한 것)."""
    done = {}
    for path in summaries.glob("*/*.md"):
        vid = path.stem.split("_", 1)[-1]
        if vid in vids:
            done[vid] = read_front_matter(path)
    # 사유 코드(no_transcript …)를 넘긴다. 표시 문구는 디스코드 쪽(discord_inbox.reason_text)이 언어에 맞춰 만든다
    failed = {r["video_id"]: r.get("reason", "") for r in site.load_failed(st.failed_file.parent) if r["video_id"] in vids}
    return done, {k: v for k, v in failed.items() if k not in done}, st.removed_ids() & vids


def git_publish(message: str) -> bool:
    """데이터 폴더의 사람별 파일(users/)을 커밋하고, 원격 저장소가 있으면 푸시한다. 실패하면 False."""
    run = partial(subprocess.run, capture_output=True, text=True, cwd=config.DATA, timeout=120)
    paths = ["users"]  # 사이트 · 상태(users/*/site · state)는 .gitignore 로 뺀다. 사이트는 Worker 에 올린다
    run(["git", "add", "-A", *paths])
    if run(["git", "diff", "--cached", "--quiet", "--", *paths]).returncode == 0:
        return True
    # 경로를 지정해 커밋한다. 수동 작업으로 스테이징해 둔 다른 변경이 섞이지 않게 한다 (2026-10-20 fbd4a73)
    c = run(["git", "commit", "-m", message, "--", *paths])
    if c.returncode != 0:
        log(f"{now_iso()} 커밋 실패: {c.stderr.strip()[-300:]}")
        return False
    if not run(["git", "remote"]).stdout.strip():
        return True
    p = git_push(config.DATA)
    if p.returncode != 0:
        log(f"{now_iso()} 푸시 실패: {p.stderr.strip()[-300:]}")
        return False
    log(f"{now_iso()} 배포: {message.splitlines()[0]}")
    return True


def git_push(cwd: Path) -> subprocess.CompletedProcess:
    """현재 브랜치를 원격에 푸시한다. 추적 브랜치가 없으면(원격 저장소를 새로 연결한 직후) 설정하면서 푸시한다.
    추적 브랜치 없이 git push 만 하면 「has no upstream branch」로 실패하고, 이후의 배포가 모두 멈춘다."""
    run = partial(subprocess.run, capture_output=True, text=True, cwd=cwd, timeout=120)
    if run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]).returncode == 0:
        return run(["git", "push", "-q"])
    remotes = run(["git", "remote"]).stdout.split()
    remote = "origin" if "origin" in remotes else remotes[0]
    return run(["git", "push", "-q", "-u", remote, "HEAD"])


def interleave(queues: list[list]) -> list:
    """사람별 대기열을 번갈아 하나씩 꺼낸 순서."""
    return [x for group in itertools.zip_longest(*queues) for x in group if x is not None]


def weekly_step(status: Status, users, now: float) -> tuple[config.User, str] | None:
    """지난주의 주간 요약을 한 건 만든다. 만들었으면 (사람, 변경 내용), 없으면 None.
    지난주 것을 모두 만들면 status 에 기록해 다음 주까지 확인하지 않는다 (요약일이 지난주인 요약은 더 생기지 않는다)."""
    key = weekly.last_week(datetime.fromtimestamp(now).astimezone())
    if status.d.get("weekly_done") == key:
        return None
    for u in users:
        with config.as_user(u):
            jobs = weekly.candidates(key)
            if not jobs:
                continue
            cat, vids = jobs[0]
            try:
                weekly.generate(key, cat, vids)
            except SummaryError as e:  # 형식 오류는 시도 횟수를 남긴다. 다음 주기에 다시 시도한다
                log(f"{now_iso()} 주간 요약 실패 ({u.name}) {key} {cat.name}: {e}")
                return None
        log(f"{now_iso()} 주간 요약 ({u.name}) {key} {cat.name} · 요약 {len(vids)}편")
        return u, f"주간 {key} {cat.name}"
    status.d["weekly_done"] = key
    return None


def _once(status: Status, key: str, message: str) -> None:
    """같은 오류는 한 번만 기록한다 (토큰 없음 등)."""
    if status.d.get(key) != message:
        log(f"{now_iso()} {message}")
    status.d[key] = message


def cycle(now: float | None = None) -> None:
    now = time.time() if now is None else now
    cfg = config.settings()["queue"]
    common = config.path("state")  # as_user 밖: 공통 상태(status.json · 잠금)
    common.mkdir(parents=True, exist_ok=True)
    status = Status(common / "status.json")
    users = config.users()
    changes: dict[str, list[str]] = {u.id: [] for u in users}

    # 2 · 3 대기열: 사람마다 디스코드 채널 조회
    dc = None
    try:
        dc = discord_inbox.Discord(discord_inbox.load_token())
        status.d.pop("discord_error", None)
    except Exception as e:  # 토큰 오류 등: 요약은 계속하고 다음 주기에 다시 조회한다
        _once(status, "discord_error", f"디스코드 조회 실패: {e}")
    poll = dc is not None and now - status.d.get("polled_at", 0) >= POLL_EVERY
    inboxes, queues = {}, []
    for u in users:
        with config.as_user(u):
            inbox = inboxes[u.id] = discord_inbox.Inbox(config.path("state"))
            if poll:
                try:
                    n = inbox.poll(dc, u.channel_id, u.owners)
                    if n:
                        log(f"{now_iso()} 디스코드에서 받음 ({u.name}) {n}건")
                    status.d.pop(f"discord_error_{u.id}", None)
                except Exception as e:
                    _once(status, f"discord_error_{u.id}", f"디스코드 조회 실패 ({u.name}): {e}")
            st = backlog.State(config.path("state"))
            queues.append([(u, it) for it in backlog.pending(inbox.items(), config.path("summaries"), st)])
    if poll:
        status.d["polled_at"] = now
    todo = interleave(queues)

    # 4 요약
    if todo and status.paused(now):
        log(f"{now_iso()} 차단으로 쉬는 중 (대기 {len(todo)}개, {datetime.fromtimestamp(status.d['pause_until']).astimezone():%H:%M} 까지)")
    elif todo:
        start, last = time.monotonic(), None
        for u, it in todo:
            if time.monotonic() - start > TIME_BUDGET:
                break
            if last is not None:
                wait = backlog.MIN_GAP_SECONDS - (time.monotonic() - last)
                if wait > 0:
                    time.sleep(wait)
            mark = []
            with config.as_user(u):
                st = backlog.State(config.path("state"))
                kind, detail = backlog.process(it, st, cfg["max_attempts"], on_fetched=lambda: mark.append(time.monotonic()))
            last = mark[0] if mark else time.monotonic()
            log(f"{now_iso()} {it['video_id']} ({u.name}) {detail}")
            if kind == "blocked":
                status.blocked(time.time())
                break
            if kind in ("claude", "tool"):  # claude -p 오류 · 프로그램 없음: 다음 주기에 다시 시도한다
                break
            status.unblocked()
            if kind in ("done", "failed"):
                changes[u.id].append(f"{'요약' if kind == 'done' else '실패'} {it['video_id']} {it.get('title', '')}")

    # 4-2 주간 요약
    if not todo or status.paused(now):
        made = weekly_step(status, users, now)
        if made:
            changes[made[0].id].append(made[1])

    # 5 사이트 조작 반영
    kv_items: dict[str, list[dict]] = {}
    if now - status.d.get("removed_checked_at", 0) >= SYNC_OPS_EVERY:
        status.d["removed_checked_at"] = now
        for u in users:
            if not u.ready:
                continue  # 사이트를 아직 열지 않았다
            with config.as_user(u):
                try:
                    items = site_ops.pending()
                except Exception as e:  # wrangler 로그인 만료 등: 다음 확인 때 다시 시도한다
                    log(f"{now_iso()} 조작 대기열 확인 실패 ({u.name}): {e}")
                    continue
                if items:
                    kv_items[u.id] = items
                    changes[u.id] += site_ops.apply(items, log=lambda m: log(f"{now_iso()} {m}"))

    # 6 사이트 생성 · 배포
    banner_changed = status.d.get("_banner") != _banner_key(status, now)
    retry = set(status.d.get("deploy_pending", []))
    changed = [u for u in users if banner_changed or changes[u.id] or u.id in kv_items or u.id in retry]
    # 사이트를 열지 않은 사람(ready = false)은 게시되지 않으므로 ✅ 와 요약 주소를 보내지 않는다
    published = {u.id: u.ready for u in users}
    if changed:
        status.d["_banner"] = _banner_key(status, now)
        status.save()  # 사이트가 최신 상태를 읽도록 먼저 저장한다
        for u in changed:
            with config.as_user(u):
                site.build(status_dir=common)
        lines = [f"{c} ({u.name})" for u in users for c in changes[u.id]]
        msg = f"파이프라인: {', '.join(c.split()[0] for c in lines) or '상태 갱신'} {len(lines)}건\n\n" + "\n".join(f"- {c}" for c in lines)
        pushed = git_publish(msg)
        for u in changed:
            ok = pushed and u.ready
            if ok:
                try:
                    deploy.worker(u)
                except Exception as e:
                    log(f"{now_iso()} 사이트 배포 실패 ({u.name}): {e}")
                    ok = False
            published[u.id] = ok
            if ok and u.id in kv_items:
                with config.as_user(u):
                    for x in kv_items[u.id]:
                        site_ops.delete(x["key"])
        status.d["deploy_pending"] = sorted(u.id for u in changed if not published[u.id] and u.ready)

    # 7 디스코드 알림: 사이트에 반영된 뒤에 요약 주소를 보낸다
    for u in users if dc else []:
        inbox = inboxes[u.id]
        items = inbox.items()
        if not (published[u.id] and items):
            continue
        with config.as_user(u):
            st = backlog.State(config.path("state"))
            done, failed, removed = results({it["video_id"] for it in items}, config.path("summaries"), st)
        try:
            n = inbox.notify(dc, u.channel_id, done, failed, removed, u.site_url, log=lambda m: log(f"{now_iso()} {m}"))
            if n:
                log(f"{now_iso()} 디스코드 알림 ({u.name}) {n}건")
        except Exception as e:
            log(f"{now_iso()} 디스코드 알림 실패 ({u.name}): {e}")
    status.save()


def _banner_key(status: Status, now: float) -> str:
    blocked_long = now - status.d.get("blocked_since", now) >= BLOCKED_BANNER_AFTER
    return f"blocked={blocked_long}"


def run_weekly(key: str | None, user_ids: list[str]) -> int:
    """`t2k weekly`: 한 주의 주간 요약을 모두 만들고 커밋 · 배포한다. 파이프라인과 같은 잠금을 기다린다."""
    key = key or weekly.last_week(local_now())
    users = [u for u in config.users() if not user_ids or u.id in user_ids]
    common = config.path("state")
    common.mkdir(parents=True, exist_ok=True)
    with open(common / "pipeline.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        made = []
        for u in users:
            with config.as_user(u):
                for cat, vids in weekly.candidates(key):
                    try:
                        log(f"주간 요약 {u.name} · {key} · {cat.name} · 요약 {len(vids)}편 → {weekly.generate(key, cat, vids)}")
                        made.append((u, cat))
                    except SummaryError as e:
                        log(f"주간 요약 실패 {u.name} · {key} · {cat.name}: {e}")
        if not made:
            log(f"{key}: 새로 만들 주간 요약이 없다 (카테고리마다 요약 {config.settings()['weekly']['min_items']}편 이상, 이미 만든 것은 건너뛴다)")
            return 0
        changed = list({u.id: u for u, _ in made}.values())
        for u in changed:
            with config.as_user(u):
                site.build(status_dir=common)
        lines = [f"주간 {key} {cat.name} ({u.name})" for u, cat in made]
        if not git_publish(f"주간 요약 {len(lines)}건\n\n" + "\n".join(f"- {x}" for x in lines)):
            return 1
        for u in changed:
            if u.ready:
                deploy.worker(u)
                log(f"배포 {u.worker}")
    return 0


def run() -> int:
    """잠금을 잡고 한 주기를 실행한다. 이미 실행 중이면 0 을 반환한다."""
    state_dir = config.path("state")
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "pipeline.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        cycle()
    return 0
