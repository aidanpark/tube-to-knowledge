"""설치 검증(코드)에서 나온 실행 쪽 수정: 원격 추적 브랜치 없는 푸시, 현지 시간대, wrangler 실행 폴더,
게시 전 사람의 ✅ 알림, claude · yt-dlp 가 없을 때. 네트워크 · YouTube · claude · 디스코드는 호출하지 않는다."""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from t2k import backlog, config, pipeline, site_ops, summarize, transcript, weekly
from tests.test_multiuser import FakeDiscord, link, root  # noqa: F401  (root 는 pytest fixture)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def ident(monkeypatch):
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "t")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "t@t")


def test_publish_to_fresh_remote_sets_upstream(data, tmp_path, ident):
    """README 대로 원격만 추가한(추적 브랜치 없는) 저장소에서도 푸시된다."""
    bare = tmp_path / "backup.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    git("init", "-q", "-b", "main", cwd=data)
    git("add", "-A", cwd=data)
    git("commit", "-q", "-m", "init", cwd=data)
    git("remote", "add", "origin", str(bare), cwd=data)
    (data / "users" / "alice" / "summaries").mkdir(parents=True, exist_ok=True)
    (data / "users" / "alice" / "summaries" / "x.md").write_text("요약")
    assert pipeline.git_publish("파이프라인: 요약 1건")
    assert git("rev-parse", "--abbrev-ref", "main@{upstream}", cwd=data).strip() == "origin/main"
    assert git("log", "-1", "--format=%s", "main", cwd=bare).strip() == "파이프라인: 요약 1건"
    (data / "users" / "alice" / "summaries" / "y.md").write_text("요약 2")
    assert pipeline.git_publish("파이프라인: 요약 2건")  # 두 번째부터는 보통의 git push
    assert git("log", "-1", "--format=%s", "main", cwd=bare).strip() == "파이프라인: 요약 2건"


def test_push_uses_first_remote_when_not_origin(tmp_path, ident):
    bare, repo = tmp_path / "b.git", tmp_path / "repo"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    (repo / "f").write_text("1")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "c", cwd=repo)
    git("remote", "add", "backup", str(bare), cwd=repo)
    assert pipeline.git_push(repo).returncode == 0
    assert git("rev-parse", "--abbrev-ref", "main@{upstream}", cwd=repo).strip() == "backup/main"


def test_wrangler_never_runs_in_data_folder(data, monkeypatch):
    seen = []
    monkeypatch.setattr(site_ops.subprocess, "run", lambda args, **kw: seen.append(kw["cwd"]) or
                        subprocess.CompletedProcess(args, 0, "[]", ""))
    with config.as_user(config.user("alice")):
        site_ops._wrangler("key", "list")
    assert seen and str(config.DATA) not in str(seen[0])


def test_not_ready_person_gets_no_link_in_later_cycle(root, monkeypatch):  # noqa: F811
    """게시 전(ready = false)에 요약한 영상: 다음 주기에도 열리지 않는 사이트 주소로 ✅ 를 보내지 않는다."""
    from tests.conftest import CONFIG
    (root / "config.toml").write_text(CONFIG.replace("ready = true\n", "ready = false\n", 1))  # 앨리스: 게시 전
    config.reload()
    dc = FakeDiscord({"100": [link("1", "AAAAAAAAAA1")], "200": []})
    monkeypatch.setattr(pipeline.discord_inbox, "load_token", lambda: "t")
    monkeypatch.setattr(pipeline.discord_inbox, "Discord", lambda token: dc)
    monkeypatch.setattr(pipeline.backlog, "MIN_GAP_SECONDS", 0)

    def process(it, st, max_attempts, on_fetched):
        d = config.path("summaries") / "0099_기타"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"2026-10-20_{it['video_id']}.md").write_text(
            '---\nvideo_id: "%s"\ntitle: "제목"\ncategory: "0099_기타"\nrating: 3\nsummarized: "2026-10-20"\n---\n' % it["video_id"])
        return "done", "0099_기타 3/5"

    monkeypatch.setattr(pipeline.backlog, "process", process)
    monkeypatch.setattr(pipeline.site, "build", lambda status_dir: None)
    monkeypatch.setattr(pipeline.deploy, "worker", lambda u: None)
    monkeypatch.setattr(pipeline, "git_publish", lambda msg: True)
    monkeypatch.setattr(pipeline.site_ops, "pending", lambda: [])
    monkeypatch.setattr(pipeline, "log", lambda *a: None)
    pipeline.cycle(now=1_000_000.0)
    pipeline.cycle(now=1_000_100.0)  # 앨리스는 이번 주기에 바뀐 것이 없다
    assert not [r for r in dc.replies if r[0] == "100"]


def test_missing_claude_is_transient(data, monkeypatch):
    monkeypatch.setattr(summarize, "_claude_bin", lambda: "/nonexistent/claude")
    with pytest.raises(summarize.SummaryError) as e:
        summarize.call_claude("요약해 줘")
    assert e.value.reason == "transient"


def test_missing_ytdlp_stops_without_retry_or_attempt(data, monkeypatch):
    monkeypatch.setattr(transcript.ytdlp_update, "ytdlp_bin", lambda: "/nonexistent/yt-dlp")
    monkeypatch.setattr(transcript.ytdlp_update, "maybe_update", lambda: pytest.fail("설치 문제로 업데이트를 확인하지 않는다"))
    tr = transcript.fetch("AAAAAAAAAAA", sleep=lambda s: pytest.fail("다시 시도하며 기다리지 않는다"))
    assert tr["status"] == "error" and tr["reason"] == "tool"
    with config.as_user(config.user("alice")):
        st = backlog.State(config.path("state"))
        kind, _ = backlog.process({"video_id": "AAAAAAAAAAA"}, st, 3, fetch=lambda vid: tr)
        assert kind == "tool" and st.attempts() == {} and not st.failed_ids()


def test_claude_failure_reuses_saved_captions(data):
    """claude 가 실패한 영상: 다음 주기에는 저장해 둔 자막을 쓰고 YouTube 에 다시 요청하지 않는다."""
    with config.as_user(config.user("alice")):
        tr = {"status": "ok", "video_id": "AAAAAAAAAAA", "title": "제목"}
        d = config.path("transcripts")
        d.mkdir(parents=True, exist_ok=True)
        (d / "AAAAAAAAAAA.json").write_text(json.dumps(tr))
        st = backlog.State(config.path("state"))
        fetched, used = [], []

        def summarize_fn(t):
            used.append(t)
            raise summarize.SummaryError("transient", "Not logged in")

        kind, _ = backlog.process({"video_id": "AAAAAAAAAAA"}, st, 3, fetch=lambda vid: fetched.append(vid),
                                  summarize_fn=summarize_fn)
        assert kind == "claude" and fetched == [] and used == [tr]


def test_local_time_and_week_boundary():
    pst = timezone(timedelta(hours=-8))
    assert weekly.last_week(datetime(2026, 10, 19, 0, 0, tzinfo=pst)) == "2026-W42"  # 그 시각의 날짜(현지 월요일)로 정한다
    assert weekly.last_week(datetime(2026, 10, 18, 23, 59, tzinfo=pst)) == "2026-W41"
    n = summarize.now()
    assert n.tzinfo is not None and n.utcoffset() == datetime.now().astimezone().utcoffset()
