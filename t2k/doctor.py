"""설치 상태 점검 (`t2k doctor`). 설치 안내의 각 단계가 되어 있는지 확인하고, 빠진 단계를 표시한다.
문구는 [general] language 를 따른다 (locales/<언어>/cli.toml 의 [doctor]).

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
from .i18n import t
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
    r.section(t("doctor.sec_tools"))
    r(OK if sys.platform == "darwin" else WARN, t("doctor.os", platform=sys.platform),
      "" if sys.platform == "darwin" else t("doctor.os_hint"))
    for tool, hint in (("git", "xcode-select --install"),
                       ("yt-dlp", "brew install yt-dlp"),
                       ("deno", t("doctor.deno_hint")),
                       ("npx", "brew install node")):
        found = shutil.which(tool)
        r(OK if found else FAIL, f"{tool}: {found or t('doctor.none')}", "" if found else hint)
    # 자동 실행(launchd)과 같은 PATH 로 시험한다. 터미널 앱의 claude 래퍼는 그 앱 밖에서 실패한다
    claude = service.find("claude") or _claude_bin()
    p = _run([claude, "-p", "--model", "haiku", "--output-format", "json", "--no-session-persistence", "--tools", ""],
             input=t("doctor.claude_prompt"), cwd=tempfile.gettempdir(), env={**os.environ, "PATH": service.search_path()})
    try:
        ok = p is not None and p.returncode == 0 and not json.loads(p.stdout).get("is_error")
    except ValueError:
        ok = False
    r(OK if ok else FAIL, t("doctor.claude", path=claude), "" if ok else t("doctor.claude_hint"))


def check_data(r: Report) -> list[config.User]:
    r.section(t("doctor.sec_data", path=config.DATA))
    if not config.config_file().exists():
        r(FAIL, t("doctor.no_config"), "t2k init")
        return []
    r(OK, "config.toml")
    head = _run(["git", "rev-parse", "HEAD"], cwd=config.DATA)
    r(OK if head and head.returncode == 0 else FAIL, t("doctor.git"),
      "" if head and head.returncode == 0 else t("doctor.git_hint"))
    remote = _run(["git", "remote"], cwd=config.DATA)
    has_remote = bool(remote and remote.stdout.strip())
    r(OK if has_remote else WARN, t("doctor.remote") if has_remote else t("doctor.no_remote"),
      "" if has_remote else t("doctor.remote_hint"))
    sub = config.settings()["site"].get("subdomain")
    r(OK if sub else FAIL, t("doctor.subdomain", value=sub or t("doctor.none")), "" if sub else t("doctor.subdomain_hint"))
    users = list(config.users())
    r(OK if users else FAIL, t("doctor.people", n=len(users), ids=", ".join(u.id for u in users)), "" if users else "t2k user add")
    for u in users:
        missing = [n for n in ("categories.toml", "profile.md") if not u.file(n).exists()]
        r(FAIL if missing else OK, t("doctor.folder", id=u.id, path=u.custom)
          + (t("doctor.folder_missing", files=", ".join(missing)) if missing else ""))
    return users


def check_discord(r: Report, users: list[config.User]) -> None:
    r.section(t("doctor.sec_discord"))
    try:
        dc = discord_inbox.Discord(discord_inbox.load_token())
        me = dc.me()
    except Exception as e:
        r(FAIL, t("doctor.token", error=e), t("doctor.token_hint", path=discord_inbox.TOKEN_FILE))
        return
    r(OK, t("doctor.bot", name=me.get("username")))
    try:
        app = dc.call("GET", "/applications/@me")
        intent = bool(app.get("flags", 0) & ((1 << 18) | (1 << 19)))  # GATEWAY_MESSAGE_CONTENT(_LIMITED)
        r(OK if intent else FAIL, "Message Content Intent",
          "" if intent else t("doctor.intent_hint"))
    except Exception as e:
        r(WARN, t("doctor.intent_unknown", error=e))
    for u in users:
        try:
            dc.messages(u.channel_id, limit=1)
            r(OK, t("doctor.channel", id=u.id, channel=u.channel_id))
        except Exception as e:
            r(FAIL, t("doctor.channel_fail", id=u.id, channel=u.channel_id, error=e), t("doctor.channel_hint"))


def check_cloudflare(r: Report, users: list[config.User]) -> None:
    r.section("Cloudflare")
    with tempfile.TemporaryDirectory() as d:  # 빈 폴더에서 실행한다 (deploy.py)
        p = _run([*WRANGLER, "whoami"], timeout=180, cwd=d)
        logged = p is not None and p.returncode == 0 and "not authenticated" not in (p.stdout + p.stderr).lower()
        r(OK if logged else FAIL, t("doctor.wrangler"), "" if logged else "npx wrangler login")
        if not logged:
            return
        for u in users:
            if not u.kv_namespace:
                r(FAIL, t("doctor.no_kv", id=u.id), t("doctor.no_kv_hint"))
                continue
            p = _run([*WRANGLER, "kv", "key", "list", "--namespace-id", u.kv_namespace, "--remote"], timeout=180, cwd=d)
            ok = p is not None and p.returncode == 0
            r(OK if ok else FAIL, f"{u.id}: KV {u.kv_namespace}",
              "" if ok else ANSI.sub("", (p.stderr or p.stdout) if p else t("doctor.timeout")).strip()[-200:])
    for u in users:
        if not u.site_url:
            continue
        ok, why = setup.access_protected(u.site_url)
        if u.ready:
            r(OK if ok else FAIL, f"{u.id}: {u.site_url} {why}",
              "" if ok else t("doctor.site_hint"))
        else:
            r(WARN, t("doctor.not_ready", id=u.id, why=why), t("doctor.not_ready_hint", id=u.id))


def check_service(r: Report) -> None:
    r.section(t("doctor.sec_service"))
    if sys.platform != "darwin":
        r(WARN, t("doctor.not_macos"))
        return
    for job in service.JOBS:
        on = service.loaded(job)
        r(OK if on else FAIL, f"{service.label(job)}", "" if on else "t2k service install")


def run(log=print) -> int:
    r = Report(log)
    check_tools(r)
    users = check_data(r)
    check_discord(r, users)
    check_cloudflare(r, users)
    check_service(r)
    log("\n" + (t("doctor.ok") if not r.failed else t("doctor.failed", n=r.failed)))
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(run())
