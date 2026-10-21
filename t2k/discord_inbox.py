"""디스코드 채널이 요약 대기열이다. 사람마다 채널 하나를 둔다.
YouTube 앱에서 공유한 링크를 채널에 보내면 파이프라인이 조회해 요약한다.

- 봇 토큰: 환경 변수 DISCORD_BOT_TOKEN, 없으면 ~/.config/t2k/discord.env 의 DISCORD_BOT_TOKEN (권한 600).
- 봇 설정: Message Content Intent 가 켜져 있어야 한다. 꺼져 있으면 봇을 언급하지 않은 메시지의
  본문 · 링크 미리보기(embed)가 빈 값으로 반환된다. 채널 권한: 메시지 읽기 · 기록 보기 · 보내기 · 반응 추가 ·

- 조회(poll): 마지막으로 읽은 메시지 이후를 읽어 state/inbox.jsonl 에 남기고 👀 를 단다.
  봇이 보낸 링크(채널 비우기 뒤 tmp 를 옮긴 것)도 입력으로 읽는다. 봇의 답장은 읽지 않는다.
- 알림(notify): 요약했으면 ✅ 와 요약 주소로, 실패했으면 ❌ 와 사유로 답장한다. 알린 메시지는 state/discord.json 에 남긴다.
- 처리 기록의 원본은 state/ 다 (summaries/ · failed.jsonl · removed.jsonl). 디스코드는 입력과 표시에만 쓴다.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .i18n import t, table
from .transcript import video_id_of

BASE = "https://discord.com/api/v10"
TOKEN_FILE = Path(os.environ.get("T2K_DISCORD_ENV", "~/.config/t2k/discord.env")).expanduser()
URL_RE = re.compile(r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/\S+", re.I)
SEEN, DONE, FAILED = "👀", "✅", "❌"


class DiscordError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message if status == 0 else f"{status}: {message}")  # 0: HTTP 응답이 아닌 오류 (토큰 파일 등)
        self.status = status


def load_token(path: Path | None = None, use_env: bool = True) -> str:
    """봇 토큰. 환경 변수 DISCORD_BOT_TOKEN 이 먼저이고, 없으면 토큰 파일. doctor 는 자동 실행처럼 파일만 읽는다(use_env=False).
    path 의 기본값은 호출할 때의 TOKEN_FILE 이다 (시험에서 바꿀 수 있게 정의할 때 고정하지 않는다)."""
    path = path or TOKEN_FILE
    if use_env and os.environ.get("DISCORD_BOT_TOKEN"):
        return os.environ["DISCORD_BOT_TOKEN"].strip()
    if not path.exists():
        raise DiscordError(0, t("token.no_file", path=path))
    for line in path.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "DISCORD_BOT_TOKEN" and value.strip():
            return value.strip().strip("'\"")
    raise DiscordError(0, t("token.no_token", path=path))


class Discord:
    def __init__(self, token: str):
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bot {token}",
                                "User-Agent": "DiscordBot (https://github.com/aidanpark/tube-to-knowledge, 0.1)"})

    def call(self, method: str, path: str, **kw) -> dict | list:
        for _ in range(3):
            r = self._s.request(method, f"{BASE}{path}", timeout=30, **kw)
            if r.status_code == 429:  # 속도 제한: 안내된 시간만큼 기다린 뒤 다시 시도한다
                time.sleep(float(r.json().get("retry_after", 1)))
                continue
            if r.status_code >= 400:
                raise DiscordError(r.status_code, r.text)
            return r.json() if r.content else {}
        raise DiscordError(429, f"속도 제한이 계속됨: {method} {path}")

    def message(self, channel_id: str, message_id: str) -> dict:
        return self.call("GET", f"/channels/{channel_id}/messages/{message_id}")

    def me(self) -> dict:
        return self.call("GET", "/users/@me")

    def messages(self, channel_id: str, after: str | None = None, limit: int = 100) -> list[dict]:
        """after 가 있으면 그 뒤의 메시지, 없으면 최근 메시지. 오래된 것부터 정렬해 반환한다."""
        params = {"limit": limit, **({"after": after} if after else {})}
        return sorted(self.call("GET", f"/channels/{channel_id}/messages", params=params), key=lambda m: int(m["id"]))

    def all_after(self, channel_id: str, after: str | None) -> list[dict]:
        """after 이후의 메시지 전부 (after 가 없으면 채널의 처음부터). 오래된 것부터."""
        out, cursor = [], after or "0"
        while True:
            page = self.messages(channel_id, cursor)
            out += page
            if len(page) < 100:
                return out
            cursor = page[-1]["id"]

    def send(self, channel_id: str, text: str) -> dict:
        return self.call("POST", f"/channels/{channel_id}/messages", json={"content": text, "allowed_mentions": {"parse": []}})

    def react(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.call("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji)}/@me")

    def unreact(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.call("DELETE", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji)}/@me")

    def reply(self, channel_id: str, message_id: str, text: str) -> dict:
        return self.call("POST", f"/channels/{channel_id}/messages", json={
            "content": text, "message_reference": {"message_id": message_id, "fail_if_not_exists": False},
            "allowed_mentions": {"parse": []}})


def video_ids(message: dict) -> list[str]:
    """메시지 본문과 링크 미리보기(embed)의 YouTube 동영상 ID. 순서를 유지하고 중복을 제거한다."""
    texts = [message.get("content") or ""]
    texts += [e.get("url") or "" for e in message.get("embeds") or []]
    found = [video_id_of(u) for t in texts for u in URL_RE.findall(t)]
    return list(dict.fromkeys(v for v in found if v))


def is_bot_reply(m: dict, bot_id: str) -> bool:
    return m["author"]["id"] == bot_id and bool(m.get("message_reference"))


def inbox(dc: Discord, channel_id: str, after: str | None, bot_id: str) -> list[dict]:
    """새 메시지 중 YouTube 링크가 있는 것: {message_id, author_id, author, video_ids}. 봇의 답장은 뺀다."""
    out = []
    for m in dc.all_after(channel_id, after):
        if is_bot_reply(m, bot_id):
            continue
        vids = video_ids(m)
        if vids:
            out.append({"message_id": m["id"], "author_id": m["author"]["id"],
                        "author": m["author"].get("global_name") or m["author"]["username"], "video_ids": vids})
    return out


def check(channel_id: str, react: bool = False, log=print) -> int:
    """토큰 · 권한 확인: 봇 이름과 채널의 최근 메시지를 표시한다. react 면 링크가 있는 최근 메시지에 👀 를 단다."""
    dc = Discord(load_token())
    bot = dc.me()
    log(t("cli.check_bot", name=bot["username"], id=bot["id"]))
    recent = dc.messages(channel_id, limit=10)  # after 없이 최근 10건
    log(t("cli.check_recent", n=len(recent)))
    for m in recent:
        body = (m.get("content") or "").replace("\n", " ")[:60]
        log(t("cli.check_message", id=m["id"], author=m["author"]["username"], body=body, links=video_ids(m)))
    others = [m for m in recent if m["author"]["id"] != bot["id"]]
    if others and not any(m.get("content") or m.get("embeds") for m in others):
        log(t("cli.check_empty"))
    found = [m for m in recent if not is_bot_reply(m, bot["id"]) and video_ids(m)]
    if react and found:
        dc.react(channel_id, found[-1]["id"], SEEN)
        log(t("cli.check_react", mark=SEEN, id=found[-1]["id"]))
    return 0


class Inbox:
    """state/inbox.jsonl (받은 링크, 메시지 · 동영상마다 한 줄) 과 state/discord.json (마지막으로 읽은 메시지, 알린 메시지)."""

    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.file = state_dir / "inbox.jsonl"
        self.meta_file = state_dir / "discord.json"
        self.meta = json.loads(self.meta_file.read_text()) if self.meta_file.exists() else {}

    def save(self) -> None:
        tmp = self.meta_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.meta, ensure_ascii=False, indent=1))
        tmp.replace(self.meta_file)

    def entries(self) -> list[dict]:
        if not self.file.exists():
            return []
        return [json.loads(line) for line in self.file.read_text().splitlines() if line.strip()]

    def items(self) -> list[dict]:
        """요약 대기열: 받은 순서대로, 같은 동영상은 처음 받은 것 하나만."""
        seen, out = set(), []
        for e in self.entries():
            if e["video_id"] not in seen:
                seen.add(e["video_id"])
                out.append(e)
        return out

    def poll(self, dc: Discord, channel_id: str, owners: tuple[str, ...] = ()) -> int:
        """새 메시지를 읽어 대기열에 넣고 👀 를 단다. 넣은 링크 수를 반환한다.
        owners 가 쓴 글 중 링크가 없는 것은 지시로 state/instructions.jsonl 에 남긴다 (agent.py 가 처리한다)."""
        if "bot_id" not in self.meta:
            self.meta["bot_id"] = dc.me()["id"]
        msgs = dc.all_after(channel_id, self.meta.get("last_id"))
        if not msgs:
            return 0
        n, seen = 0, []
        with self.file.open("a") as f:
            for m in msgs:
                if is_bot_reply(m, self.meta["bot_id"]):
                    continue
                if video_ids(m):
                    seen.append(m["id"])
                elif m["author"]["id"] in owners and (m.get("content") or "").strip():
                    self.add_instruction(m)
                for vid in video_ids(m):
                    rec = {"video_id": vid, "message_id": m["id"], "author_id": m["author"]["id"],
                           "author": m["author"].get("global_name") or m["author"]["username"],
                           "time": m.get("timestamp", "")}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
        self.meta["last_id"] = msgs[-1]["id"]
        self.save()  # 반응을 달기 전에 저장한다. 반응이 실패해도 같은 메시지를 다시 넣지 않는다
        for mid in seen:
            try:
                dc.react(channel_id, mid, SEEN)
            except DiscordError:
                pass  # 표시일 뿐이다
        return n

    def add_instruction(self, m: dict) -> None:
        rec = {"message_id": m["id"], "author_id": m["author"]["id"], "text": m["content"].strip(),
               "time": m.get("timestamp", ""),
               "reply_to": (m.get("message_reference") or {}).get("message_id")}  # 답장이면 앞 대화를 잇는다 (agent.py)
        with (self.file.parent / "instructions.jsonl").open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def instructions(self) -> list[dict]:
        f = self.file.parent / "instructions.jsonl"
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]

    def notify(self, dc: Discord, channel_id: str, done: dict[str, dict], failed: dict[str, str],
               removed: set[str], site_url: str, log=print) -> int:
        """결과가 나온 메시지에 반응 · 답장한다. done: 동영상 ID → 요약 앞머리, failed: 동영상 ID → 사유."""
        notified = set(self.meta.get("notified", []))
        n = 0
        for e in self.entries():
            key = f"{e['message_id']}:{e['video_id']}"
            vid = e["video_id"]
            if key in notified or not (vid in done or vid in failed or vid in removed):
                continue
            try:
                if vid in done:
                    s = done[vid]
                    text = t("discord.notify.done", title=s.get("title", ""), category=s.get("category", "").split("_", 1)[-1],
                             rating=s.get("rating", "?"), url=f"{site_url.rstrip('/')}/v/{vid}.html")
                    mark = DONE
                elif vid in failed:
                    text, mark = t("discord.notify.failed", reason=reason_text(failed[vid])), FAILED
                else:
                    text, mark = None, DONE  # 삭제한 요약: 이미 처리한 것으로 표시만 한다
                dc.react(channel_id, e["message_id"], mark)
                try:
                    dc.unreact(channel_id, e["message_id"], SEEN)
                except DiscordError:
                    pass
                if text:
                    dc.reply(channel_id, e["message_id"], text)
            except DiscordError as err:
                if err.status != 404:  # 지워진 메시지는 알린 것으로 친다
                    log(f"디스코드 알림 실패 ({vid}): {err}")
                    continue
            notified.add(key)
            n += 1
        self.meta["notified"] = sorted(notified)
        self.save()
        return n


def reason_text(reason: str) -> str:
    """실패 사유를 지금 언어로. reason 은 사유 코드(no_transcript …) 또는 한국어 표시 이름(site.load_failed 의 reason_ko)."""
    codes = table("discord.failed")
    if reason in codes:
        return codes[reason]
    by_ko = {v: k for k, v in table("discord.failed", "ko").items()}
    return codes[by_ko[reason]] if reason in by_ko else reason


def snowflake_time(message_id: str) -> float:
    return ((int(message_id) >> 22) + 1420070400000) / 1000
