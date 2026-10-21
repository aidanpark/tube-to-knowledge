"""설치 상태 점검 (`ytbook doctor`). 설치 안내의 각 단계가 되어 있는지 확인하고, 빠진 단계를 표시한다.

외부 시스템에 요청을 보내지만 아무것도 바꾸지 않는다. Claude 확인은 짧은 요청 한 번(haiku)을 보낸다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import requests

from . import config, discord_inbox, service, setup
from .site_ops import ANSI, WRANGLER
from .summarize import _claude_bin

OK, WARN, FAIL = "✓", "!", "✗"


class Report:
    def __init__(self, log=print):
        self.log, self.failed = log, 0

    def __call__(self, mark: str, what: str, hint: str = "") -> None:
        if mark == FAIL:
            self.failed += 1
        self.log(f" {mark} {what}" + (f"\n     → {hint}" if hint else ""))

    def section(self, title: str) -> None:
        self.log(f"\n[{title}]")


def _run(args: list[str], timeout: int = 120, **kw) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kw)
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_tools(r: Report) -> None:
    r.section("프로그램")
    r(OK if sys.platform == "darwin" else WARN, f"운영체제: {sys.platform}",
      "" if sys.platform == "darwin" else "자동 실행(launchd)과 디스코드 지시(agent)는 macOS 에서만 지원한다")
    for tool, hint in (("git", "xcode-select --install"),
                       ("yt-dlp", "brew install yt-dlp"),
                       ("deno", "brew install deno (yt-dlp 가 YouTube 를 처리할 때 쓰는 JavaScript 실행기)"),
                       ("npx", "brew install node")):
        found = shutil.which(tool)
        r(OK if found else FAIL, f"{tool}: {found or '없음'}", "" if found else hint)
    # 자동 실행(launchd)과 같은 PATH 로 시험한다. 터미널 앱의 claude 래퍼는 그 앱 밖에서 실패한다
    claude = service.find("claude") or _claude_bin()
    p = _run([claude, "-p", "--model", "haiku", "--output-format", "json", "--no-session-persistence", "--tools", ""],
             input="OK 라고만 답한다.", cwd=tempfile.gettempdir(), env={**os.environ, "PATH": service.search_path()})
    try:
        ok = p is not None and p.returncode == 0 and not json.loads(p.stdout).get("is_error")
    except ValueError:
        ok = False
    r(OK if ok else FAIL, f"Claude Code 로그인 ({claude})",
      "" if ok else "Claude Code 를 설치하고 claude 를 한 번 실행해 로그인한다")


def check_data(r: Report) -> list[config.User]:
    r.section(f"데이터 폴더 {config.DATA}")
    if not config.config_file().exists():
        r(FAIL, "config.toml 없음", "ytbook init")
        return []
    r(OK, "config.toml")
    head = _run(["git", "rev-parse", "HEAD"], cwd=config.DATA)
    r(OK if head and head.returncode == 0 else FAIL, "git 저장소 (커밋 1개 이상)",
      "" if head and head.returncode == 0 else "ytbook init (git 사용자 이름 · 이메일 설정 필요)")
    remote = _run(["git", "remote"], cwd=config.DATA)
    has_remote = bool(remote and remote.stdout.strip())
    r(OK if has_remote else WARN, "원격 저장소 (백업)" if has_remote else "원격 저장소 없음: 요약은 이 컴퓨터에만 남는다",
      "" if has_remote else "비공개 저장소를 만들어 git remote add origin ... (선택)")
    sub = config.settings()["site"].get("subdomain")
    r(OK if sub else FAIL, f"workers.dev 하위 도메인: {sub or '없음'}", "" if sub else "config.toml 의 [site] subdomain")
    users = list(config.users())
    r(OK if users else FAIL, f"사람 {len(users)}명: {', '.join(u.id for u in users)}", "" if users else "ytbook user add")
    for u in users:
        missing = [n for n in ("categories.toml", "profile.md") if not u.file(n).exists()]
        r(FAIL if missing else OK, f"{u.id}: 폴더 {u.custom}" + (f" (없음: {', '.join(missing)})" if missing else ""))
    return users


def check_discord(r: Report, users: list[config.User]) -> None:
    r.section("디스코드")
    try:
        dc = discord_inbox.Discord(discord_inbox.load_token())
        me = dc.me()
    except Exception as e:
        r(FAIL, f"봇 토큰: {e}", f"토큰을 {discord_inbox.TOKEN_FILE} 에 DISCORD_BOT_TOKEN=... 로 저장한다 (권한 600)")
        return
    r(OK, f"봇: {me.get('username')}")
    try:
        app = dc.call("GET", "/applications/@me")
        intent = bool(app.get("flags", 0) & ((1 << 18) | (1 << 19)))  # GATEWAY_MESSAGE_CONTENT(_LIMITED)
        r(OK if intent else FAIL, "Message Content Intent",
          "" if intent else "개발자 포털 → Bot → Privileged Gateway Intents → Message Content Intent 를 켠다")
    except Exception as e:
        r(WARN, f"Message Content Intent 를 확인하지 못함: {e}")
    for u in users:
        try:
            dc.messages(u.channel_id, limit=1)
            r(OK, f"{u.id}: 채널 {u.channel_id} 읽기")
        except Exception as e:
            r(FAIL, f"{u.id}: 채널 {u.channel_id} 읽기 실패: {e}",
              "봇을 서버에 초대하고, 채널 권한(보기 · 기록 보기 · 보내기 · 반응 추가)을 준다")


def check_cloudflare(r: Report, users: list[config.User]) -> None:
    r.section("Cloudflare")
    with tempfile.TemporaryDirectory() as d:  # 빈 폴더에서 실행한다 (deploy.py)
        p = _run([*WRANGLER, "whoami"], timeout=180, cwd=d)
        logged = p is not None and p.returncode == 0 and "not authenticated" not in (p.stdout + p.stderr).lower()
        r(OK if logged else FAIL, "wrangler 로그인", "" if logged else "npx wrangler login")
        if not logged:
            return
        for u in users:
            if not u.kv_namespace:
                r(FAIL, f"{u.id}: 조작 대기열 KV 없음", "ytbook user add 로 추가한 사람만 KV 가 있다")
                continue
            p = _run([*WRANGLER, "kv", "key", "list", "--namespace-id", u.kv_namespace, "--remote"], timeout=180, cwd=d)
            ok = p is not None and p.returncode == 0
            r(OK if ok else FAIL, f"{u.id}: KV {u.kv_namespace}",
              "" if ok else ANSI.sub("", (p.stderr or p.stdout) if p else "시간 초과").strip()[-200:])
    for u in users:
        if not u.site_url:
            continue
        ok, why = setup.access_protected(u.site_url)
        if u.ready:
            r(OK if ok else FAIL, f"{u.id}: {u.site_url} {why}",
              "" if ok else "사이트가 Access 없이 열린다! Cloudflare 대시보드에서 Access 설정을 확인한다")
        else:
            r(WARN, f"{u.id}: 아직 열지 않음 (ready = false). {why}",
              f"Access 를 설정한 뒤 ytbook user ready {u.id}")


def check_service(r: Report) -> None:
    r.section("자동 실행 (launchd)")
    if sys.platform != "darwin":
        r(WARN, "macOS 가 아님: 확인하지 않음")
        return
    for job in service.JOBS:
        on = service.loaded(job)
        r(OK if on else FAIL, f"{service.label(job)}", "" if on else "ytbook service install")


def run(log=print) -> int:
    r = Report(log)
    check_tools(r)
    users = check_data(r)
    check_discord(r, users)
    check_cloudflare(r, users)
    check_service(r)
    log(f"\n{'문제 없음' if not r.failed else f'확인할 항목 {r.failed}개'}")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(run())
