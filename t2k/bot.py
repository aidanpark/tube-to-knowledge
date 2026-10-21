"""디스코드 실시간 봇. launchd 가 상시 실행한다 (`t2k bot`, KeepAlive).

디스코드 게이트웨이에 계속 접속해 있다가, 사람의 채널에 owners(config.toml)가 링크 없는 글(지시)을 쓰면
1. 바로 👀 를 달고,
2. 그 사람의 state/instructions.jsonl 에 남긴 뒤,
3. 지시 처리 작업(launchd 의 agent 작업, service.py)을 즉시 깨운다.

이 봇이 하는 일은 여기까지다. 지시의 처리 · 승인 · 적용은 agent.py, 링크 요약은 pipeline.py 가 한다.
봇이 끊긴 동안 온 지시는 파이프라인의 1분 주기 읽기(discord_inbox.poll)가 기록하므로 빠지지 않는다.
같은 지시가 두 번 기록되어도 agent 가 메시지 ID 로 한 번만 처리한다.
"""
import os
import subprocess
from datetime import datetime
from functools import partial

import discord

from . import config, discord_inbox, service
from .summarize import now as local_now

log = partial(print, flush=True)


def now_iso() -> str:
    return local_now().isoformat(timespec="seconds")


def as_record(m: discord.Message) -> dict:
    """discord.py 메시지를 REST 응답과 같은 모양으로 바꾼다 (Inbox.add_instruction 이 받는 형식)."""
    ref = m.reference.message_id if m.reference and m.reference.message_id else None
    return {"id": str(m.id), "author": {"id": str(m.author.id), "username": m.author.name},
            "content": m.content or "", "timestamp": m.created_at.isoformat(),
            "message_reference": {"message_id": str(ref)} if ref else None}


def wake_agent() -> None:
    """지시 처리 작업을 바로 실행한다. 이미 실행 중이면 launchd 가 아무것도 하지 않는다 (그 주기가 이어서 처리한다)."""
    subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{service.label('agent')}"],
                   capture_output=True, timeout=30)


def handle(rec: dict, users: tuple[config.User, ...], channel_id: str) -> config.User | None:
    """지시이면 그 사람의 지시 목록에 넣고 그 사람을 반환한다. 지시가 아니면 None."""
    u = next((u for u in users if u.channel_id == channel_id), None)
    if u is None or rec["author"]["id"] not in u.owners:
        return None
    text = rec["content"].strip()
    if not text or discord_inbox.video_ids(rec):
        return None  # 빈 글 · 링크는 파이프라인이 처리한다
    with config.as_user(u):
        discord_inbox.Inbox(config.path("state")).add_instruction(rec)
    return u


class Bot(discord.Client):
    def __init__(self, wake=wake_agent):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True  # 개발자 포털에서 Message Content Intent 를 켜야 한다
        super().__init__(intents=intents)
        self.wake = wake

    async def on_ready(self):
        log(f"{now_iso()} 접속: {self.user} · 채널 {len(config.users())}개")

    async def on_message(self, m: discord.Message):
        if m.author.bot:
            return
        rec = as_record(m)
        u = handle(rec, config.users(), str(m.channel.id))
        if u is None:
            return
        log(f"{now_iso()} 지시 ({u.name}): {rec['content'][:80]}")
        try:
            await m.add_reaction(discord_inbox.SEEN)
        except discord.HTTPException:
            pass  # 표시일 뿐이다
        self.wake()


def run() -> int:
    Bot().run(discord_inbox.load_token(), log_handler=None)  # 끊기면 discord.py 가 다시 접속한다
    return 0
