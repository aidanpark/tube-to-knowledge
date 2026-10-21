"""yt-dlp 가 연속으로 실패하면 GitHub 최신 릴리스를 확인하고, 새 판이 있으면 Homebrew 로 업데이트한다.

YouTube 가 내부 구조를 바꾸면 yt-dlp 의 새 판이 나올 때까지 받기가 실패한다. 실패 원인이 그것일 때 사람이 개입하지 않아도 회복되게 하기 위해서다.
확인 기록은 state/ytdlp_update.json 에 남기고, update_check_hours 안에는 다시 확인하지 않는다.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

import requests

from . import config

RELEASES = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"


def brew_bin() -> str | None:
    return config.settings()["ytdlp"].get("brew") or shutil.which("brew")


def ytdlp_bin() -> str:
    brew = brew_bin()
    return shutil.which("yt-dlp") or (str(Path(brew).parent / "yt-dlp") if brew else "yt-dlp")


def version_key(v: str) -> tuple[int, ...]:
    """'2026.08.19' · '2026.8.19' · '2026.08.19.1' 을 비교 가능한 튜플로 바꾼다."""
    return tuple(int(x) for x in v.strip().lstrip("v").split(".") if x.isdigit())


def installed_version() -> str:
    return subprocess.run([ytdlp_bin(), "--version"], capture_output=True, text=True, timeout=30).stdout.strip()


def latest_version() -> str:
    r = requests.get(RELEASES, headers={"Accept": "application/vnd.github+json"}, timeout=30)
    r.raise_for_status()
    return r.json()["tag_name"]


def _state_file() -> Path:
    with config.as_user(None):  # 사람과 관계없이 하나 (공통 state/)
        return config.path("state") / "ytdlp_update.json"


def maybe_update(now: float | None = None) -> bool:
    """새 판으로 업데이트했으면 True. 확인 간격 안이거나, 새 판이 없거나, 업데이트에 실패하면 False."""
    now = time.time() if now is None else now
    cfg = config.settings()["ytdlp"]
    sf = _state_file()
    state = json.loads(sf.read_text()) if sf.exists() else {}
    if now - state.get("checked_at", 0) < cfg["update_check_hours"] * 3600:
        return False
    rec = {"checked_at": now}
    try:
        rec["installed"] = before = installed_version()
        rec["latest"] = latest = latest_version()
        if version_key(latest) <= version_key(before):
            rec["result"] = "최신 판"
            return False
        brew = brew_bin()
        if not brew:
            rec["result"] = "새 판이 있으나 Homebrew 가 없어 업데이트하지 않음 (yt-dlp 를 직접 업데이트한다)"
            return False
        # brew upgrade 는 formula 목록을 먼저 갱신한다. Homebrew 반영이 GitHub 릴리스보다 늦으면 판이 바뀌지 않는다.
        p = subprocess.run([brew, "upgrade", "yt-dlp"], capture_output=True, text=True, timeout=900)
        rec["after"] = after = installed_version()
        changed = version_key(after) > version_key(before)
        rec["result"] = "업데이트함" if changed else f"업데이트 안 됨 (brew 종료 코드 {p.returncode}, Homebrew 미반영 가능성)"
        return changed
    except Exception as e:  # 확인 실패가 파이프라인을 멈추지 않게 한다
        rec["result"] = f"확인 실패: {type(e).__name__}: {str(e)[:200]}"
        return False
    finally:
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
