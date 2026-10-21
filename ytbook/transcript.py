"""자막과 동영상 정보를 yt-dlp 로 확보한다. YouTube Data API 할당량을 쓰지 않는다.

출력 규약은 smykla-skalski/sai 의 youtube-summary `fetch_transcript.py` (MIT, Copyright (c) 2025 Smykla Skalski, THIRD_PARTY_NOTICES.md) 를 따른다:
JSON 한 개, status ok|error, 종료 코드 0 성공 · 1 자막 실패 · 2 잘못된 입력. 코드는 새로 작성했다.

yt-dlp 만 사용하고 대체 경로는 두지 않는다.
1. `yt-dlp -J` 로 동영상 정보와 자막 목록을 확보한다 (동영상 페이지 요청).
2. 자막을 선택한다: 수동 자막(languages 순서) → 원어 자동 자막. 기계 번역 자동 자막은 쓰지 않는다.
3. `--load-info-json` 으로 1의 정보를 재사용하여 그 자막만 json3 형식으로 받는다 (자막 요청 1회).
transient 이면 attempts 회까지 시도하고, 모두 실패하면 GitHub 의 yt-dlp 새 판을 확인하여 업데이트한 뒤 한 번 더 시도한다.

실패 사유(reason): no_transcript · unavailable 은 재시도해도 소용없다.
blocked(429 · 봇 확인)는 재시도하지 않고 업데이트도 확인하지 않는다. 차단 중의 요청은 차단을 늘리므로 즉시 반환하고, 파이프라인 전체를 멈춘다.
transient(그 밖의 오류)만 재시도한다.
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config, ytdlp_update

ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_RES = [
    re.compile(r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})"),
]
NO_RETRY = {"no_transcript", "unavailable", "blocked"}
CHAPTER_SECONDS = 30
BLOCKED_RE = re.compile(r"HTTP Error 429|Too Many Requests|Sign in to confirm|not a bot", re.I)
UNAVAILABLE_RE = re.compile(r"Private video|Video unavailable|has been removed|account .* terminated|"
                            r"members-only|This video is not available|Join this channel", re.I)


class TranscriptError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def video_id_of(url_or_id: str) -> str | None:
    s = url_or_id.strip()
    if ID_RE.match(s):
        return s
    for r in URL_RES:
        m = r.search(s)
        if m:
            return m.group(1)
    return None


def fmt_time(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def classify(stderr: str) -> str:
    if BLOCKED_RE.search(stderr):
        return "blocked"
    if UNAVAILABLE_RE.search(stderr):
        return "unavailable"
    return "transient"


def _ytdlp(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run([ytdlp_update.ytdlp_bin(), "--no-warnings", "--no-playlist", *args],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise TranscriptError("transient", f"yt-dlp 시간 초과 ({timeout}초)") from e
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        raise TranscriptError(classify(err), err.splitlines()[-1][:300] if err else "yt-dlp 실패")
    return p


def info(video_id: str, workdir: Path) -> dict:
    p = _ytdlp(["-J", f"https://www.youtube.com/watch?v={video_id}"], 120)
    (workdir / "info.json").write_text(p.stdout)
    return json.loads(p.stdout)


def metadata(video_id: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        return meta_of(info(video_id, Path(d)))


def meta_of(d: dict) -> dict:
    up = d.get("upload_date") or ""
    return {
        "title": d.get("title") or "",
        "author": d.get("channel") or d.get("uploader") or "",
        "duration": int(d.get("duration") or 0),
        "published": f"{up[:4]}-{up[4:6]}-{up[6:8]}" if len(up) == 8 else "",
        "description": (d.get("description") or "")[:5000],  # 관련 링크를 고르는 데 쓴다
        "categories": d.get("categories") or [],  # YouTube 분류. "Music" 이면 음악 형식 후보 (summarize)
        "music": music_of(d),
    }


def music_of(d: dict) -> dict:
    """YouTube 가 붙인 곡 정보(「이 동영상의 음악」). 곡명(track)이 있을 때만 쓴다.
    곡명이 없는 영상의 release_year 는 곡이 아니라 영상의 공개 연도라서 넣지 않는다."""
    if not d.get("track"):
        return {}
    artist = d.get("artist") or ", ".join(d.get("artists") or []) or d.get("creator") or ""
    return {k: v for k, v in {"track": d["track"], "artist": artist, "album": d.get("album"),
                              "release_year": d.get("release_year")}.items() if v}


def _base(key: str) -> str:
    """'ko-tgb1yGiJu6Y'(이름 붙은 수동 자막) · 'ko-orig' → 'ko'. 'zh-Hans' 같은 지역 표기는 앞부분만 본다."""
    return key.split("-")[0]


def pick_track(d: dict, langs: list[str]) -> tuple[str, bool] | None:
    """(자막 키, 자동 생성 여부). 수동 자막(languages 순서) → 원어 자동 자막 → 아무 수동 자막."""
    manual = [k for k in (d.get("subtitles") or {}) if k != "live_chat"]
    for lang in langs:
        for k in manual:
            if _base(k) == lang:
                return k, False
    auto = d.get("automatic_captions") or {}
    orig = [k for k in auto if k.endswith("-orig")]
    if orig:
        return orig[0], True
    if d.get("language") in auto:
        return d["language"], True
    return (manual[0], False) if manual else None


def parse_json3(text: str) -> list[dict]:
    segs = []
    for ev in json.loads(text).get("events", []):
        t = "".join(s.get("utf8", "") for s in ev.get("segs") or []).replace("\n", " ").strip()
        if t:
            segs.append({"start": ev.get("tStartMs", 0) / 1000, "text": t})
    return segs


def _subtitles(d: dict, workdir: Path, key: str, generated: bool) -> list[dict]:
    _ytdlp(["--load-info-json", str(workdir / "info.json"), "--skip-download",
            "--write-auto-subs" if generated else "--write-subs", "--sub-langs", key, "--sub-format", "json3",
            "-o", str(workdir / "%(id)s")], 180)
    files = sorted(workdir.glob("*.json3"))
    if not files:
        raise TranscriptError("transient", f"자막 파일이 만들어지지 않음 ({key})")
    segs = parse_json3(files[0].read_text())
    if not segs:
        raise TranscriptError("no_transcript", f"자막이 비어 있음 ({key})")
    return segs


def _attempt(vid: str, langs: list[str]) -> tuple[dict, list[dict], str, bool]:
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        d = info(vid, w)
        track = pick_track(d, langs)
        if track is None:
            if "Music" in (d.get("categories") or []):
                return meta_of(d), [], "", False  # 음악 영상은 자막 없이 곡 정보만 정리한다
            raise TranscriptError("no_transcript", "사용 가능한 자막 없음")
        key, generated = track
        return meta_of(d), _subtitles(d, w, key, generated), _base(key), generated


def _with_retry(vid: str, langs: list[str], cfg: dict, sleep) -> tuple[dict, list[dict], str, bool]:
    """transient 이면 attempts 회까지 시도한다. 모두 실패하면 yt-dlp 새 판을 확인하여, 업데이트되었으면 한 번 더 시도한다.
    no_transcript · unavailable · blocked 는 즉시 다시 던진다."""
    waits = cfg["retry_wait_seconds"]
    for i in range(cfg["attempts"]):
        if i:
            sleep(waits[min(i - 1, len(waits) - 1)])
        try:
            return _attempt(vid, langs)
        except TranscriptError as e:
            if e.reason in NO_RETRY:
                raise
            last = e
    if ytdlp_update.maybe_update():
        return _attempt(vid, langs)
    raise last


def fetch(url_or_id: str, sleep=time.sleep) -> dict:
    """규약에 맞는 dict 를 반환한다. 실패해도 예외를 던지지 않고 status=error 를 반환한다."""
    vid = video_id_of(url_or_id)
    if not vid:
        return {"status": "error", "reason": "invalid", "error": f"YouTube 동영상이 아님: {url_or_id}"}
    langs = config.settings()["summary"]["languages"]
    cfg = config.settings()["ytdlp"]
    out = {"status": "ok", "video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}"}
    try:
        meta, segs, lang, generated = _with_retry(vid, langs, cfg, sleep)
    except TranscriptError as e:
        return {**out, "status": "error", "reason": e.reason, "error": str(e)}
    out.update(meta)
    out["short"] = 0 < out["duration"] <= config.settings()["summary"]["short_max_seconds"]
    if out["short"]:
        out["url"] = f"https://www.youtube.com/shorts/{vid}"
    out["duration_human"] = fmt_time(out["duration"])
    out["language"] = lang
    out["is_generated"] = generated
    out["segment_count"] = len(segs)
    out["transcript"] = " ".join(s["text"].replace("\n", " ") for s in segs)
    chapters, block, block_start = [], [], None
    for s in segs:
        if block_start is None:
            block_start = s["start"]
        block.append(s["text"].replace("\n", " "))
        if s["start"] - block_start >= CHAPTER_SECONDS:
            chapters.append(f"[{fmt_time(block_start)}] {' '.join(block)}")
            block, block_start = [], None
    if block:
        chapters.append(f"[{fmt_time(block_start)}] {' '.join(block)}")
    out["chapters"] = chapters
    return out


def main(argv: list[str]) -> int:
    r = fetch(argv[0] if argv else "")
    print(json.dumps(r, ensure_ascii=False))
    if r["status"] == "ok":
        return 0
    return 2 if r.get("reason") == "invalid" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
