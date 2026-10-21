"""디스코드 답장의 영어: 알림 문구, 실패 사유, 승인 · 취소 말, 지시 응대 프롬프트. 디스코드 · claude 는 호출하지 않는다."""
import pytest

from t2k import agent, config, discord_inbox as di
from tests.conftest import CONFIG
from tests.test_agent import FakeDiscord as AgentDiscord, ins
from tests.test_discord_inbox import FakeDiscord, msg


@pytest.fixture
def english(data):
    (data / "config.toml").write_text(CONFIG.replace('language = "ko"', 'language = "en"'))
    config.reload()
    yield
    config.reload()


def test_notify_in_english(tmp_path, english):
    dc = FakeDiscord([msg("10", "bobuser", "https://youtu.be/AAAAAAAAAAA"), msg("11", "bobuser", "https://youtu.be/BBBBBBBBBBB"),
                      msg("12", "bobuser", "https://youtu.be/CCCCCCCCCCC")])
    ib = di.Inbox(tmp_path)
    ib.poll(dc, "ch")
    done = {"AAAAAAAAAAA": {"title": "Title", "category": "0099_Other", "rating": 4}}
    # 실패 사유는 코드로도, 사이트의 한국어 표시 이름으로도 온다
    ib.notify(dc, "ch", done, {"BBBBBBBBBBB": "자막 없음", "CCCCCCCCCCC": "blocked"}, set(), "https://example.dev/")
    assert dc.replies[0] == ("10", "✅ Title\nOther · Rating 4/5\nhttps://example.dev/v/AAAAAAAAAAA.html")
    assert ("11", "❌ Could not summarize: No subtitles") in dc.replies
    assert ("12", "❌ Could not summarize: Blocked by YouTube") in dc.replies


def test_reason_text_unknown_passes_through(english):
    assert di.reason_text("뭔가 다른 사유") == "뭔가 다른 사유"


def test_english_decision_words(tmp_path, english):
    sk = config.user("bob")
    st = agent.State(tmp_path / "agent.json")
    agent.ingest(st, sk, [ins("1", "make the text bigger"), ins("2", "add a category")])
    st.tasks[0]["status"] = "awaiting"
    agent.ingest(st, sk, [ins("3", "Approve!")])
    assert st.tasks[0]["decision"] == "approve" and len(st.tasks) == 2  # 결정은 새 지시가 아니다
    st.tasks[1]["status"] = "awaiting"
    agent.ingest(st, sk, [ins("4", "cancel.")])
    assert st.tasks[1]["decision"] == "cancel"
    agent.ingest(st, sk, [ins("5", "승인")])  # 영어 설정에서 한국어 말은 새 지시다
    assert st.tasks[-1]["text"] == "승인"


def test_korean_decision_words_unchanged(tmp_path, data):
    sk = config.user("bob")
    st = agent.State(tmp_path / "agent.json")
    agent.ingest(st, sk, [ins("1", "글씨를 크게")])
    st.tasks[0]["status"] = "awaiting"
    agent.ingest(st, sk, [ins("2", "approve")])  # 한국어 설정은 지금과 같다: 영어 말은 새 지시
    assert "decision" not in st.tasks[0] and st.tasks[-1]["text"] == "approve"
    agent.ingest(st, sk, [ins("3", "적용")])
    assert st.tasks[0]["decision"] == "approve"


def test_agent_prompt_and_fixed_replies_in_english(english):
    u = config.user("bob")
    p = agent.build_prompt(u, "make it bigger", [("bobuser", "how many?"), ("Assistant", "12")])
    assert "write 2-6 lines in polite, plain English" in p and "Earlier conversation" in p
    assert p.rstrip().endswith("make it bigger") and "[Assistant] 12" in p and "한국어" not in p
    dc = AgentDiscord()
    task = {"id": "7", "user": "bob", "message_id": "7", "status": "awaiting"}
    agent.discard(task, dc)
    assert dc.replies[-1][2] == "Cancelled. The changes were discarded."


def test_agent_security_flags_unchanged():
    assert agent.ISOLATION == ["--permission-mode", "dontAsk", "--setting-sources", "project", "--strict-mcp-config"]
    assert agent.ALLOW == ["Read(./**)", "Edit(./**)", "Write(./**)"]
    assert {"Bash", "WebFetch", "WebSearch"} <= set(agent.DENY) and "Read(~/**)" in agent.DENY
