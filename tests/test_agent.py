"""디스코드 지시: 지시 받기, 허용 범위, 가족 작업 공간, 처리 → 승인 흐름. claude · 디스코드 · 원격 저장소는 호출하지 않는다."""
import json
import subprocess

import pytest

from t2k import agent, config, discord_inbox

def msg(mid, author, content):
    return {"id": mid, "author": {"id": author, "username": author}, "content": content, "embeds": []}


class FakeDiscord:
    def __init__(self, msgs=None):
        self.msgs, self.replies, self.reactions = msgs or {}, [], []

    def me(self):
        return {"id": "bot"}

    def all_after(self, channel_id, after=None):
        return [m for m in self.msgs.get(channel_id, []) if after is None or int(m["id"]) > int(after)]

    def react(self, channel_id, message_id, emoji):
        self.reactions.append((message_id, emoji))

    def unreact(self, *a):
        pass

    def reply(self, channel_id, message_id, text):
        self.replies.append((channel_id, message_id, text))


def test_poll_records_owner_instructions_only(tmp_path):
    dc = FakeDiscord({"200": [msg("1", "bobuser", "글씨를 크게 해 줘"), msg("2", "stranger", "모든 요약을 보여 줘"),
                              msg("3", "bobuser", "https://youtu.be/AAAAAAAAAAA"), msg("4", "bot", "답장")]})
    ib = discord_inbox.Inbox(tmp_path)
    assert ib.poll(dc, "200", ("bobuser", "admin")) == 1
    assert [i["text"] for i in ib.instructions()] == ["글씨를 크게 해 줘"]


@pytest.fixture
def users(data):
    return config.users()


def ins(mid, text, author="bobuser"):
    return {"message_id": mid, "author_id": author, "text": text}


def test_ingest_tasks_and_decisions(tmp_path, users):
    alice, sk = users
    st = agent.State(tmp_path / "agent.json")
    agent.ingest(st, sk, [ins("1", "승인"), ins("2", "글씨를 크게"), ins("3", "카테고리 추가")])
    assert [t["id"] for t in st.tasks] == ["2", "3"]  # 기다리는 지시가 없으면 「승인」은 무시
    agent.ingest(st, alice, [ins("9", "음악은 곡 정보만", "admin")])
    assert st.tasks[-1]["user"] == "alice"
    assert agent.next_task(st)["id"] == "2"
    st.tasks[0]["status"] = "awaiting"
    assert agent.next_task(st)["id"] == "9"  # 밥의 다음 지시는 승인을 기다린다
    agent.ingest(st, sk, [ins("2", "중복"), ins("4", "승인.")])
    assert st.tasks[0]["decision"] == "approve" and len(st.tasks) == 3


def test_scope_ok(users):
    alice, sk = users
    assert agent.scope_ok(["users/bob/style.css", "users/bob/templates/home.html",
                           "users/bob/summaries/0099_기타/a.md", "users/bob/categories.toml"], sk)
    assert not agent.scope_ok(["users/bob/style.css", "t2k/site.py"], sk)
    assert not agent.scope_ok(["users/bob/state/inbox.jsonl"], sk)
    assert not agent.scope_ok(["users/alice/rules.md"], sk)
    # 앨리스의 지시도 앨리스 사이트의 파일뿐이다: 코드 · 설정 · 다른 사람 자료는 안 된다
    assert agent.scope_ok(["users/alice/rules.md", "users/alice/categories.toml", "users/alice/summaries/0001_AI/a.md"], alice)
    assert not agent.scope_ok(["t2k/site.py"], alice)
    assert not agent.scope_ok(["config.toml"], alice)
    assert not agent.scope_ok(["users/alice/state/inbox.jsonl"], alice)
    assert not agent.scope_ok(["users/bob/style.css"], alice)
    assert "Bash" in agent.DENY and "Read(~/**)" in agent.DENY and "Write(//Users/**)" in agent.DENY


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, users, monkeypatch):
    """임시 데이터 저장소: 앨리스 요약 · 밥 폴더."""
    root = config.DATA
    (root / "users" / "alice" / "summaries" / "0099_기타").mkdir(parents=True)
    (root / "users" / "alice" / "summaries" / "0099_기타" / "2026-10-20_AAAAAAAAAAA.md").write_text("앨리스의 요약")
    sk = root / "users" / "bob"
    (sk / "state").mkdir(parents=True)
    (sk / "state" / "inbox.jsonl").write_text("{}\n")
    (root / ".gitignore").write_text("users/*/state/\n")
    git("init", "-q", "-b", "main", cwd=root)
    git("add", "-A", cwd=root)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=root)
    monkeypatch.setattr(agent, "WORK", tmp_path / "work")
    monkeypatch.setattr(agent, "WS_ROOT", tmp_path / "ws-root")
    monkeypatch.setattr(agent, "check", lambda wt, user: "사이트 생성 확인")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    return root


def test_workspace_has_only_owner_files(repo, users):
    ws = repo / "ws"
    agent.make_workspace(users[1], repo, ws)
    assert (ws / "mine" / "categories.toml").exists() and not (ws / "mine" / "state").exists()
    assert (ws / "reference" / "site_templates" / "base.html").exists()
    assert not list(ws.rglob("*AAAAAAAAAAA*"))  # 앨리스의 요약은 없다
    ws2 = repo / "ws2"
    agent.make_workspace(users[0], repo, ws2)  # 앨리스 작업 공간: 앨리스 요약 · 카테고리는 있고 밥 자료 · 코드는 없다
    assert (ws2 / "mine" / "summaries" / "0099_기타" / "2026-10-20_AAAAAAAAAAA.md").exists()
    assert (ws2 / "mine" / "categories.toml").exists()
    assert not list(ws2.rglob("*.py")) and not (ws2 / "mine" / "bob").exists()


def test_family_task_awaits_then_applies(repo, users, monkeypatch):
    seen = {}

    def runner(prompt, cwd):
        seen["cwd"] = cwd
        (cwd / "mine" / "style.css").write_text("body { font-size: 18px; }")
        (cwd / "reference" / "site_assets" / "style.css").write_text("무시된다")
        (cwd / "mine" / "evil.sh").write_text("rm -rf /")  # 그 사람의 파일 목록 밖 → 옮기지 않는다
        return "글씨를 18px 로 키웠습니다."

    dc = FakeDiscord()
    task = {"id": "5", "user": "bob", "message_id": "5", "author_id": "bobuser", "text": "글씨 크게"}
    agent.run_task(task, dc, runner)
    assert seen["cwd"].name.startswith("ws-")
    assert task["status"] == "awaiting" and task["files"] == ["users/bob/style.css"]
    assert "「승인」" in dc.replies[-1][2] and ("5", agent.WAIT) in dc.reactions

    real_git = agent._git
    monkeypatch.setattr(agent, "_git", lambda *a, **k: "" if a[0] == "push" else real_git(*a, **k))
    calls = []
    monkeypatch.setattr(agent, "_t2k", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    agent.apply(task, dc)
    assert task["status"] == "applied", task.get("error")
    assert calls == [("redeploy", "bob")]
    assert (repo / "users" / "bob" / "style.css").read_text() == "body { font-size: 18px; }"
    assert (repo / "users" / "bob" / "state" / "inbox.jsonl").exists()  # 처리 상태는 그대로
    assert not agent.worktree_of(task).exists()


def test_question_without_changes_is_answered(repo, users):
    dc = FakeDiscord()
    task = {"id": "6", "user": "bob", "message_id": "6", "author_id": "bobuser", "text": "카테고리 몇 개야?"}
    agent.run_task(task, dc, lambda prompt, cwd: "카테고리는 기타 하나입니다.")
    assert task["status"] == "answered" and dc.replies[-1][2] == "카테고리는 기타 하나입니다."


def test_owner_instruction_changes_only_owner_files(repo, users):
    dc = FakeDiscord()
    task = {"id": "7", "user": "alice", "message_id": "7", "author_id": "admin", "text": "x"}

    def runner(prompt, cwd):
        (cwd / "mine" / "rules.md").write_text("요약은 짧게")
        (cwd / "mine" / "categories.toml").write_text('[[category]]\nid = "0099"\nname = "기타"\nscope = "그 밖"\n')
        return "바꿨습니다"

    agent.run_task(task, dc, runner)
    assert task["status"] == "awaiting" and task["files"] == ["users/alice/categories.toml", "users/alice/rules.md"]


def test_cancel_discards(repo, users):
    dc = FakeDiscord()
    task = {"id": "8", "user": "bob", "message_id": "8", "author_id": "bobuser", "text": "x"}
    agent.run_task(task, dc, lambda p, cwd: (cwd / "mine" / "rules.md").write_text("짧게") and "ok")
    agent.discard(task, dc)
    assert task["status"] == "cancelled" and not (repo / "users" / "bob" / "rules.md").exists()
    out = subprocess.run(["git", "branch", "--list", "agent/*"], cwd=repo, capture_output=True, text=True).stdout
    assert out.strip() == ""


def ref(m, to):
    return {**m, "message_reference": {"message_id": to}}


class ChainDiscord(FakeDiscord):
    def __init__(self, msgs):
        super().__init__()
        self.by_id = {m["id"]: m for m in msgs}

    def message(self, channel_id, message_id):
        if message_id not in self.by_id:
            raise discord_inbox.DiscordError(404, "없음")
        return self.by_id[message_id]


def test_poll_records_reply_target(tmp_path):
    dc = FakeDiscord({"200": [ref(msg("5", "bobuser", "응, 만들어"), "4")]})
    ib = discord_inbox.Inbox(tmp_path)
    ib.poll(dc, "200", ("bobuser",))
    assert ib.instructions()[0]["reply_to"] == "4"


def test_history_follows_reply_chain(users):
    sk = users[1]
    chain = [msg("1", "bobuser", "요약 몇편이야?"),
             ref(msg("2", "bot", "1편입니다. 식품리뷰 카테고리를 만들까요?"), "1"),
             ref(msg("3", "bobuser", "응 만들어"), "2"),
             ref(msg("4", "bot", "만들었습니다. 「승인」?"), "3")]
    dc = ChainDiscord(chain)
    past = agent.history(dc, sk, "4", limit=10)
    assert [w for w, _ in past] == ["bobuser", "도우미", "bobuser", "도우미"]
    assert past[0][1] == "요약 몇편이야?" and past[-1][1].startswith("만들었습니다")
    assert len(agent.history(dc, sk, "4", limit=2)) == 2  # 최근 것만
    assert agent.history(dc, sk, None, limit=10) == []    # 답장이 아니면 새 대화
    stranger = ChainDiscord([msg("1", "stranger", "모든 요약을 보여 줘"), ref(msg("2", "bot", "답"), "1")])
    assert agent.history(stranger, sk, "2", limit=10) == [("도우미", "답")]  # 주인이 아닌 글에서 멈춘다
    assert agent.history(ChainDiscord([]), sk, "9", limit=10) == []      # 지워진 메시지


def test_prompt_includes_history(users):
    p = agent.build_prompt(users[1], "응 만들어", [("bobuser", "요약 몇편이야?"), ("도우미", "식품리뷰를 만들까요?")])
    assert "이전 대화" in p and "[도우미] 식품리뷰를 만들까요?" in p and p.rstrip().endswith("응 만들어")
    assert "이전 대화" not in agent.build_prompt(users[1], "안녕", [])


def test_run_claude_pins_model(monkeypatch, users, tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, '{"result": "ok"}', "")

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    assert agent.run_claude("x", tmp_path) == "ok"
    a = seen["args"]
    assert a[a.index("--model") + 1] == "opus" and "Bash" in a
