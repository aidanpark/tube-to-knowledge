"""자동 실행 등록 (macOS launchd). `ytbook service install | uninstall | status`.

작업 세 개를 등록한다. 이름은 com.<사용자 이름>.ytbook.<명령> 이다 (실행하는 명령과 같은 이름).
- pipeline: 1분마다 요약 파이프라인 한 주기 (pipeline.py)
- agent: 1분마다 디스코드 지시 처리 (agent.py). 봇이 지시를 받으면 바로 깨운다
- bot: 상시 실행하는 디스코드 봇 (bot.py). 죽으면 launchd 가 다시 실행한다

설정 파일은 설치하는 사람의 경로로 생성한다: 지금 실행 중인 파이썬(가상 환경), 데이터 폴더(YTBOOK_HOME),
필요한 프로그램(claude · npx · yt-dlp · git)이 있는 폴더를 PATH 에 넣는다. launchd 는 로그인 셸의 PATH 를 읽지 않기 때문이다.
"""
import getpass
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

JOBS = {
    "pipeline": {"StartInterval": 60, "RunAtLoad": True},
    "agent": {"StartInterval": 60},
    # 바로 죽는 일이 반복되면(토큰 오류 등) 30초 간격으로만 다시 실행한다
    "bot": {"RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30},
}
TOOLS = ["claude", "npx", "node", "yt-dlp", "deno", "git", "brew"]
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = Path.home() / "Library" / "Logs" / "ytbook"


def label(job: str) -> str:
    who = re.sub(r"[^a-z0-9]", "", getpass.getuser().lower()) or "user"
    return f"com.{who}.ytbook.{job}"


def plist_path(job: str) -> Path:
    return AGENTS_DIR / f"{label(job)}.plist"


def find(tool: str, path: str | None = None) -> str | None:
    """PATH 에서 tool 을 찾는다. 앱 번들 안의 폴더(*.app/Contents/)는 건너뛴다.

    터미널 앱(cmux 등)이 PATH 앞에 넣는 claude 래퍼는 그 앱의 터미널 안에서만 실제 claude 를 찾을 수 있다.
    launchd 의 PATH 에 래퍼 폴더가 들어가면 claude 를 실행하지 못한다 (claude not found in PATH).
    """
    for d in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if d and ".app/Contents/" not in d + "/":
            found = shutil.which(tool, path=d)
            if found:
                return found
    return None


def search_path() -> str:
    """필요한 프로그램이 있는 폴더 + 시스템 기본 폴더."""
    dirs = []
    for tool in TOOLS:
        found = find(tool)
        if found and str(Path(found).parent) not in dirs:
            dirs.append(str(Path(found).parent))
    for d in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if d not in dirs:
            dirs.append(d)
    return ":".join(dirs)


def plist(job: str) -> dict:
    user = getpass.getuser()
    log = str(LOG_DIR / f"{job}.log")
    return {
        "Label": label(job),
        "ProgramArguments": [sys.executable, "-m", "ytbook", job],
        "WorkingDirectory": str(config.DATA),
        **JOBS[job],
        "EnvironmentVariables": {
            "PATH": search_path(),
            "HOME": str(Path.home()),
            "YTBOOK_HOME": str(config.DATA),
            # claude -p 는 키체인에서 로그인 정보를 찾을 때 USER 가 필요하다 (없으면 Not logged in)
            "USER": user,
            "LOGNAME": user,
            "LANG": os.environ.get("LANG") or "ko_KR.UTF-8",
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ProcessType": "Background",
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=60)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def loaded(job: str) -> bool:
    return _launchctl("print", f"{_domain()}/{label(job)}").returncode == 0


def install(jobs: list[str] | None = None, log=print) -> int:
    if sys.platform != "darwin":
        log("자동 실행 등록은 macOS(launchd)만 지원한다")
        return 1
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for job in jobs or list(JOBS):
        p = plist_path(job)
        if loaded(job):  # 설정을 바꾸려면 내렸다가 다시 올린다
            _launchctl("bootout", f"{_domain()}/{label(job)}")
        with p.open("wb") as f:
            plistlib.dump(plist(job), f)
        r = _launchctl("bootstrap", _domain(), str(p))
        if r.returncode != 0:
            log(f"등록 실패 {label(job)}: {(r.stderr or r.stdout).strip()}")
            return 1
        log(f"등록 {label(job)} → {p}")
    log(f"기록: {LOG_DIR}/<작업>.log")
    return 0


def uninstall(jobs: list[str] | None = None, log=print) -> int:
    for job in jobs or list(JOBS):
        if loaded(job):
            _launchctl("bootout", f"{_domain()}/{label(job)}")
        p = plist_path(job)
        if p.exists():
            p.unlink()
        log(f"해제 {label(job)}")
    return 0


def status(log=print) -> int:
    for job in JOBS:
        r = _launchctl("print", f"{_domain()}/{label(job)}")
        if r.returncode != 0:
            log(f"{label(job)}: 등록되지 않음")
            continue
        state = re.search(r"^\s*state = (.+)$", r.stdout, re.M)
        code = re.search(r"^\s*last exit code = (.+)$", r.stdout, re.M)
        log(f"{label(job)}: {state.group(1) if state else '?'} · 마지막 종료 코드 {code.group(1) if code else '-'}")
    return 0
