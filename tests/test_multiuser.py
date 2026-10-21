"""사람별 구조: 경로 · 카테고리 · 관심사 분리, 번갈아 처리, 사람별 배포 · 알림. 디스코드 · YouTube · claude · git 은 호출하지 않는다."""
import json

import pytest

from ytbook import config, pipeline, summarize
from tests.test_summarize import TR


@pytest.fixture
def root(data):
    return data


def test_paths_and_categories_per_user(root):
    alice, sk = config.users()
    assert config.path("summaries") == root / "users/alice/summaries"  # as_user 밖은 첫 번째 사람
    with config.as_user(sk):
        assert config.path("summaries") == root / "users/bob/summaries"
        assert config.path("transcripts") == root / "transcripts"  # 자막 기록은 함께 쓴다
        assert [c.key for c in config.categories()] == ["0099_기타"]
        config.add_category("요리", "집에서 만드는 요리")
        assert [c.name for c in config.categories()] == ["요리", "기타"]
        with config.as_user(None):
            assert config.path("state") == root / "state"
    with config.as_user(alice):
        assert len(config.categories()) > 2 and config.path("site") == root / "users/alice/site"
    assert "요리" not in (root / "users/alice/categories.toml").read_text()


def test_prompt_uses_profile_and_categories_of_user(root):
    tr = {**TR, "chapters": ["[00:00] 안녕"], "short": False}
    alice, sk = config.users()
    with config.as_user(sk):
        p = summarize.build_prompt(tr)
    assert "요리와 여행" in p and "업무 자동화" not in p and "0001_AI_개발도구" not in p
    p = summarize.build_prompt(tr)  # as_user 밖은 앨리스
    assert "업무 자동화" in p and "0001_AI_개발도구" in p and "{{PROFILE}}" not in p


def test_interleave():
    assert pipeline.interleave([[1, 2, 3], ["a"], []]) == [1, "a", 2, 3]


class FakeDiscord:
    def __init__(self, msgs):
        self.msgs, self.replies = msgs, []

    def me(self):
        return {"id": "bot"}

    def all_after(self, channel_id, after=None):
        return [m for m in self.msgs[channel_id] if after is None or int(m["id"]) > int(after)]

    def react(self, *a):
        pass

    def unreact(self, *a):
        pass

    def reply(self, channel_id, message_id, text):
        self.replies.append((channel_id, text))


def link(mid, vid):
    return {"id": mid, "author": {"id": "u", "username": "u"}, "content": f"https://youtu.be/{vid}", "embeds": []}


def test_cycle_alternates_users_and_deploys_family_site(root, monkeypatch):
    dc = FakeDiscord({"100": [link("1", "AAAAAAAAAA1"), link("2", "AAAAAAAAAA2")], "200": [link("3", "SSSSSSSSSS1")]})
    monkeypatch.setattr(pipeline.discord_inbox, "load_token", lambda: "t")
    monkeypatch.setattr(pipeline.discord_inbox, "Discord", lambda token: dc)
    monkeypatch.setattr(pipeline.backlog, "MIN_GAP_SECONDS", 0)
    order = []

    def process(it, st, max_attempts, on_fetched):
        u = config.current_user()
        order.append((u.id, it["video_id"]))
        d = config.path("summaries") / "0099_기타"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"2026-10-20_{it['video_id']}.md").write_text(
            '---\nvideo_id: "%s"\ntitle: "제목"\ncategory: "0099_기타"\nrating: 3\nsummarized: "2026-10-20"\n---\n' % it["video_id"])
        return "done", "0099_기타 3/5"

    monkeypatch.setattr(pipeline.backlog, "process", process)
    built, deployed, commits = [], [], []
    monkeypatch.setattr(pipeline.site, "build", lambda status_dir: built.append(config.current_user().id))
    monkeypatch.setattr(pipeline.deploy, "worker", lambda u: deployed.append(u.id))
    monkeypatch.setattr(pipeline, "git_publish", lambda msg: commits.append(msg) or True)
    monkeypatch.setattr(pipeline.site_ops, "pending", lambda: [])
    monkeypatch.setattr(pipeline, "log", lambda *a: None)

    pipeline.cycle(now=1_000_000.0)
    assert order == [("alice", "AAAAAAAAAA1"), ("bob", "SSSSSSSSSS1"), ("alice", "AAAAAAAAAA2")]
    assert sorted(built) == ["alice", "bob"] and deployed == ["alice", "bob"]
    assert "(밥)" in commits[0]
    urls = {ch: text.splitlines()[-1] for ch, text in dc.replies}
    assert urls == {"100": "https://a.example/v/AAAAAAAAAA2.html", "200": "https://s.example/v/SSSSSSSSSS1.html"}
    assert (root / "users/bob/state/inbox.jsonl").exists() and (root / "users/alice/state/inbox.jsonl").exists()


def test_failed_family_deploy_is_retried(root, monkeypatch):
    dc = FakeDiscord({"100": [], "200": []})
    monkeypatch.setattr(pipeline.discord_inbox, "load_token", lambda: "t")
    monkeypatch.setattr(pipeline.discord_inbox, "Discord", lambda token: dc)
    monkeypatch.setattr(pipeline.site, "build", lambda status_dir: None)
    monkeypatch.setattr(pipeline, "git_publish", lambda msg: True)
    monkeypatch.setattr(pipeline.site_ops, "pending", lambda: [])
    monkeypatch.setattr(pipeline, "log", lambda *a: None)
    calls = []

    def worker(u):
        calls.append(u.id)
        if u.id == "bob" and calls.count("bob") == 1:
            raise RuntimeError("네트워크")

    monkeypatch.setattr(pipeline.deploy, "worker", worker)
    pipeline.cycle(now=1_000_000.0)  # 상태 안내가 처음 정해져 모두 다시 생성한다 → 밥 배포 실패
    assert json.loads((root / "state/status.json").read_text())["deploy_pending"] == ["bob"]
    pipeline.cycle(now=1_000_100.0)
    assert calls == ["alice", "bob", "bob"]
    assert json.loads((root / "state/status.json").read_text())["deploy_pending"] == []


def test_not_ready_user_is_not_deployed(root, monkeypatch):
    from tests.conftest import CONFIG
    (root / "config.toml").write_text(CONFIG.replace('ready = true\n', 'ready = false\n', 1))
    config.reload()
    dc = FakeDiscord({"100": [], "200": []})
    monkeypatch.setattr(pipeline.discord_inbox, "load_token", lambda: "t")
    monkeypatch.setattr(pipeline.discord_inbox, "Discord", lambda token: dc)
    monkeypatch.setattr(pipeline.site, "build", lambda status_dir: None)
    monkeypatch.setattr(pipeline, "git_publish", lambda msg: True)
    pending = []
    monkeypatch.setattr(pipeline.site_ops, "pending", lambda: pending.append(config.current_user().id) or [])
    monkeypatch.setattr(pipeline, "log", lambda *a: None)
    deployed = []
    monkeypatch.setattr(pipeline.deploy, "worker", lambda u: deployed.append(u.id))
    pipeline.cycle(now=1_000_000.0)
    assert deployed == ["bob"] and pending == ["bob"]  # Access 를 확인하기 전(앨리스)은 올리지 않는다


def test_worker_config(root):
    from ytbook import deploy
    c = deploy.worker_config(config.users()[1], root / "users/bob/site")
    assert c["name"] == "s" and c["preview_urls"] is False and c["workers_dev"] is True
    assert c["kv_namespaces"] == [{"binding": "OPS", "id": "kv-s"}]
    assert c["assets"]["directory"].endswith("users/bob/site") and c["main"].endswith("worker/index.js")


def test_custom_layer_templates_css_rules(root):
    from datetime import datetime
    from ytbook import site
    from ytbook.summarize import KST
    alice, sk = config.users()
    c = root / "users" / "bob"
    (c / "templates").mkdir(parents=True)
    (c / "templates" / "categories.html").write_text('{% extends "base.html" %}{% block body %}<p>밥 전용 카테고리 화면</p>{% endblock %}')
    (c / "style.css").write_text("body { font-size: 18px; }")
    (c / "rules.md").write_text("- 요리 영상은 재료 목록을 정리한다")
    with config.as_user(sk):
        out = site.build(out=root / "s-site", state=root / "s-state", now=datetime(2026, 10, 21, tzinfo=KST))
        p = summarize.build_prompt({**TR, "chapters": ["[0:00] 안녕"], "short": False})
    assert "밥 전용 카테고리 화면" in (out / "categories.html").read_text()
    assert (out / "assets" / "user.css").read_text() == "body { font-size: 18px; }"
    assert "assets/user.css" in (out / "index.html").read_text()
    assert "재료 목록" in p
    with config.as_user(alice):  # 다른 사람에게는 적용되지 않는다
        out = site.build(out=root / "a-site", state=root / "a-state", now=datetime(2026, 10, 21, tzinfo=KST))
        p = summarize.build_prompt({**TR, "chapters": ["[0:00] 안녕"], "short": False})
    assert "밥 전용" not in (out / "categories.html").read_text() and not (out / "assets" / "user.css").exists()
    assert "재료 목록" not in p and "(없음)" in p


def test_user_templates_are_sandboxed(root):
    from datetime import datetime
    from jinja2.exceptions import SecurityError
    from ytbook import site
    from ytbook.summarize import KST
    sk = config.users()[1]
    t = root / "users" / "bob" / "templates"
    t.mkdir(parents=True)
    (t / "categories.html").write_text("{{ ''.__class__.__mro__[1].__subclasses__() }}")
    with config.as_user(sk), pytest.raises(SecurityError):
        site.build(out=root / "s-site", state=root / "s-state", now=datetime(2026, 10, 21, tzinfo=KST))
