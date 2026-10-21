"""ytbook 명령. 데이터 폴더는 환경 변수 YTBOOK_HOME (기본값 ~/ytbook-data).

설치: init · user add · user ready · service install · doctor
운영(자동 실행이 호출한다): pipeline · agent · bot
수동: summarize · transcript · site · redeploy · sync-ops · remove · move · discord-check
"""
import argparse
import json
import sys
from pathlib import Path

from . import config


def cmd_summarize(target: str, transcript_file: str | None) -> int:
    from . import summarize, transcript

    if transcript_file:
        # 수동 처리: 직접 확보한 자막 텍스트로 요약한다 (자막 없는 동영상 등)
        vid = transcript.video_id_of(target)
        if not vid:
            print(f"YouTube 동영상이 아님: {target}", file=sys.stderr)
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
        print(f"{path}\n{s['category']} · ♪ {s['format']} {s['shortSummary']}")
    else:
        print(f"{path}\n{s['category']} · {s['rating']}/5 {s['recommendation']} · 수명 {s['lifespan']}")
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
        print(f"배포 {u.worker}")
    return 0


COMMANDS = ["init", "user", "service", "doctor", "pipeline", "agent", "bot", "summarize", "transcript", "site",
            "redeploy", "sync-ops", "remove", "move", "discord-check", "check-site"]


def main() -> None:
    ap = argparse.ArgumentParser(prog="ytbook", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=COMMANDS)
    ap.add_argument("target", nargs="*", help="명령의 대상 (user add · user ready · service install · 동영상 URL · ID)")
    ap.add_argument("--user", help="summarize · site · remove · move: 대상 사람 id (기본값: 첫 번째 사람)")
    ap.add_argument("--transcript", help="summarize: 직접 확보한 자막 텍스트 파일")
    ap.add_argument("--id", help="user add: 사람 id (영문 소문자 · 숫자)")
    ap.add_argument("--name", help="user add: 화면에 쓰는 이름")
    ap.add_argument("--channel", help="user add: 디스코드 채널 ID")
    ap.add_argument("--owner", action="append", default=[], help="user add: 지시를 받을 디스코드 사용자 ID (여러 번 쓸 수 있다)")
    ap.add_argument("--react", action="store_true", help="discord-check: 링크가 있는 최근 메시지에 👀 반응을 단다")
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
            ap.error("ytbook service install | uninstall | status")
        sys.exit(fn())
    if a.cmd == "user":
        from . import setup
        if sub == "add":
            if not (a.id and a.name and a.channel and a.owner):
                ap.error("ytbook user add --id ID --name 이름 --channel 채널ID --owner 사용자ID [--owner ...]")
            sys.exit(setup.add_user(a.id, a.name, a.channel, a.owner))
        if sub == "ready" and len(a.target) == 2:
            sys.exit(setup.ready(a.target[1]))
        if sub == "list":
            for u in config.users():
                print(f"{u.id}\t{u.name}\t{'열림' if u.ready else '준비 중'}\t{u.site_url}")
            return
        ap.error("ytbook user add ... | user ready ID | user list")
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
            print(f"디스코드 확인 실패: {e}", file=sys.stderr)
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
                print(f"이동: {line} → {sub}")
        elif a.cmd == "remove":
            from . import remove
            for r in remove.remove(a.target):
                print(f"삭제: {r['video_id']} {r['title']}")


if __name__ == "__main__":
    main()
