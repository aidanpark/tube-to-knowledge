"""t2k 명령. 데이터 폴더는 환경 변수 T2K_HOME (기본값 ~/t2k-data).

설치: init · user add · user ready · service install · doctor
운영(자동 실행이 호출한다): pipeline · agent · bot
수동: summarize · weekly · transcript · site · redeploy · sync-ops · remove · move · discord-check

화면에 보이는 도움말 · 안내는 [general] language 를 따른다 (locales/<언어>/cli.toml 의 [cli]).
"""
import argparse
import json
import re
import sys
from pathlib import Path

from . import config
from .i18n import t


def cmd_summarize(target: str, transcript_file: str | None) -> int:
    from . import summarize, transcript

    if transcript_file:
        # 수동 처리: 직접 확보한 자막 텍스트로 요약한다 (자막 없는 동영상 등)
        vid = transcript.video_id_of(target)
        if not vid:
            print(t("cli.not_video", target=target), file=sys.stderr)
            return 2
        meta = transcript.metadata(vid)
        text = Path(transcript_file).read_text()
        tr = {"status": "ok", "video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}", **meta,
              "language": "manual", "is_generated": False, "transcript": text,
              "chapters": [line for line in text.splitlines() if line.strip()]}
        tr["short"] = 0 < tr["duration"] <= config.settings()["summary"]["short_max_seconds"]
        tr["duration_human"] = transcript.fmt_time(tr["duration"])
    else:
        tr = transcript.fetch(target)
        if tr["status"] != "ok":
            print(json.dumps(tr, ensure_ascii=False), file=sys.stderr)
            return 2 if tr.get("reason") == "invalid" else 1
    path, s = summarize.summarize(tr)
    if s.get("kind") == "music":
        fmt = t("md.format." + s["format"])
        print(f"{path}\n{s['category']} · ♪ {fmt} {s['shortSummary']}")
    else:
        print(f"{path}\n" + t("cli.summary_line", category=s["category"], rating=s["rating"],
                                recommendation=t(f"md.recommendation.{s['recommendation']}"),
                                lifespan=t(f"md.lifespan.{s['lifespan']}")))
    return 0


def redeploy(ids: list[str]) -> int:
    from . import deploy, site
    common = config.path("state")
    for u in config.users():
        if (ids and u.id not in ids) or not u.ready:
            continue
        with config.as_user(u):
            site.build(status_dir=common)
        deploy.worker(u)
        print(t("cli.deployed", worker=u.worker))
    return 0


COMMANDS = ["init", "user", "service", "doctor", "pipeline", "agent", "bot", "summarize", "weekly", "transcript", "site",
            "redeploy", "sync-ops", "remove", "move", "discord-check", "check-site"]


def main() -> None:
    ap = argparse.ArgumentParser(prog="t2k", description=t("cli.description"), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=COMMANDS)
    ap.add_argument("target", nargs="*", help=t("cli.help_target"))
    ap.add_argument("--user", help=t("cli.help_user"))
    ap.add_argument("--transcript", help=t("cli.help_transcript"))
    ap.add_argument("--id", help=t("cli.help_id"))
    ap.add_argument("--name", help=t("cli.help_name"))
    ap.add_argument("--channel", help=t("cli.help_channel"))
    ap.add_argument("--owner", action="append", default=[], help=t("cli.help_owner"))
    ap.add_argument("--react", action="store_true", help=t("cli.help_react"))
    a = ap.parse_args()
    sub = a.target[0] if a.target else ""

    if a.cmd == "init":
        from . import setup
        sys.exit(setup.init())
    if a.cmd == "doctor":
        from . import doctor
        sys.exit(doctor.run())
    if a.cmd == "service":
        from . import service
        fn = {"install": service.install, "uninstall": service.uninstall, "status": service.status}.get(sub)
        if not fn:
            ap.error(t("cli.usage_service"))
        sys.exit(fn())
    if a.cmd == "user":
        from . import setup
        if sub == "add":
            if not (a.id and a.name and a.channel and a.owner):
                ap.error(t("cli.usage_user_add"))
            sys.exit(setup.add_user(a.id, a.name, a.channel, a.owner))
        if sub == "ready" and len(a.target) == 2:
            sys.exit(setup.ready(a.target[1]))
        if sub == "list":
            for u in config.users():
                print(f"{u.id}\t{u.name}\t{t('cli.user_open') if u.ready else t('cli.user_pending')}\t{u.site_url}")
            return
        ap.error(t("cli.usage_user"))
    if a.cmd == "check-site":
        from . import setup
        sys.exit(setup.check_site(sub))
    if a.cmd == "pipeline":
        from . import pipeline
        sys.exit(pipeline.run())
    if a.cmd == "bot":
        from . import bot
        sys.exit(bot.run())
    if a.cmd == "agent":
        from . import agent
        sys.exit(agent.run())
    if a.cmd == "weekly":
        from . import pipeline
        if sub and not re.fullmatch(r"\d{4}-W\d{2}", sub):
            ap.error(t("cli.usage_weekly"))
        sys.exit(pipeline.run_weekly(sub or None, [a.user] if a.user else []))
    if a.cmd == "redeploy":
        sys.exit(redeploy(a.target))
    if a.cmd == "transcript":
        from . import transcript
        sys.exit(transcript.main([sub]))
    if a.cmd == "discord-check":
        from . import discord_inbox
        try:
            for u in config.users():
                print(f"== {u.name} ({u.channel_id})")
                discord_inbox.check(u.channel_id, a.react)
        except discord_inbox.DiscordError as e:
            print(t("cli.discord_check_failed", error=e), file=sys.stderr)
            sys.exit(1)
        return
    if a.cmd == "sync-ops":
        from . import site_ops
        for u in config.users():
            if not u.ready:
                continue
            with config.as_user(u):
                print(f"== {u.name}")
                site_ops.run()
        return

    # 이하는 한 사람의 파일을 다룬다
    with config.as_user(config.user(a.user) if a.user else config.default_user()):
        if a.cmd == "summarize":
            sys.exit(cmd_summarize(sub, a.transcript))
        if a.cmd == "site":
            from . import site
            print(site.build())
        elif a.cmd == "move":
            from . import recategorize
            for line in recategorize.move(a.target[1:], sub):
                print(t("cli.moved", line=line, to=sub))
        elif a.cmd == "remove":
            from . import remove
            for r in remove.remove(a.target):
                print(t("cli.removed", id=r["video_id"], title=r["title"]))


if __name__ == "__main__":
    main()
