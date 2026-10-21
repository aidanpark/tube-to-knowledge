"""실시간 봇: 지시만 골라 기록한다. 디스코드에는 접속하지 않는다."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from t2k import bot, config, discord_inbox


@pytest.fixture
def users(data):
    return config.users()


def rec(author, content, mid="1"):
    return {"id": mid, "author": {"id": author, "username": author}, "content": content,
            "timestamp": "2026-10-21T00:00:00+00:00", "message_reference": None}


def test_handle_records_owner_instructions_only(users, tmp_path):
    assert bot.handle(rec("bobuser", "글씨 크게"), users, "200").id == "bob"
    assert bot.handle(rec("stranger", "요약 보여 줘"), users, "200") is None
    assert bot.handle(rec("bobuser", "https://youtu.be/AAAAAAAAAAA"), users, "200") is None  # 링크는 파이프라인
    assert bot.handle(rec("bobuser", "  "), users, "200") is None
    assert bot.handle(rec("bobuser", "글씨 크게"), users, "999") is None  # 모르는 채널
    assert bot.handle(rec("bobuser", "앨리스 채널에 쓴 글"), users, "100") is None  # 앨리스 채널의 주인이 아니다
    with config.as_user(users[1]):
        ins = discord_inbox.Inbox(config.path("state")).instructions()
    assert [i["text"] for i in ins] == ["글씨 크게"]


def test_as_record_keeps_reply_target():
    m = SimpleNamespace(id=5, author=SimpleNamespace(id=7, name="bob"), content="응 만들어",
                        created_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
                        reference=SimpleNamespace(message_id=4))
    r = bot.as_record(m)
    assert r["id"] == "5" and r["author"]["id"] == "7" and r["message_reference"] == {"message_id": "4"}
    m.reference = None
    assert bot.as_record(m)["message_reference"] is None


def test_agent_drains_queue_in_one_wake(users, monkeypatch):
    from t2k import agent
    calls = []
    results = iter([True, True, False])
    monkeypatch.setattr(agent, "step", lambda dc=None, runner=None: calls.append(1) or next(results))
    agent.cycle()
    assert len(calls) == 3
