"""대기열 처리 시험. 가짜 fetch · summarize 를 쓰며 yt-dlp · claude · 네트워크는 호출하지 않는다."""
import json

import pytest

from ytbook import backlog
from ytbook.summarize import SummaryError


def item(vid, title="제목"):
    return {"video_id": vid, "title": title}


def ok(vid):
    return {"status": "ok", "video_id": vid}


def err(reason, msg="x"):
    return {"status": "error", "reason": reason, "error": msg}


@pytest.fixture
def env(tmp_path):
    """fetch 는 동영상별 대본대로 응답하고, 요청 시각 · 대기 · 출력을 기록한다."""
    rec = {"t": 0.0, "fetches": [], "sleeps": [], "lines": [], "script": {}, "summarize_error": {}}

    def fetch(vid):
        rec["fetches"].append((vid, rec["t"]))
        rec["t"] += 5  # 자막 확보에 5초 걸린다고 가정한다
        return rec["script"].get(vid, ok(vid))

    def summarize_fn(tr):
        rec["t"] += 30
        e = rec["summarize_error"].get(tr["video_id"])
        if e:
            raise e
        return None, {"category": "0001_AI_개발도구", "rating": 3}

    def sleep(s):
        rec["sleeps"].append(s)
        rec["t"] += s

    def run(items, limit=None):
        return backlog.run(items, limit, summaries_dir=tmp_path / "summaries", state_dir=tmp_path / "state",
                           fetch=fetch, summarize_fn=summarize_fn, clock=lambda: rec["t"], sleep=sleep,
                           out=rec["lines"].append)

    rec["run"] = run
    rec["state"] = backlog.State(tmp_path / "state")
    rec["summaries"] = tmp_path / "summaries"
    return rec


def failed(env):
    f = env["state"].failed_file
    return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []


def test_skips_summarized_and_failed(env):
    d = env["summaries"] / "0001_AI_개발도구"
    d.mkdir(parents=True)
    (d / "2026-10-19_aaaaaaa_aaa.md").write_text("")
    env["state"].fail(item("bbbbbbbbbbb"), "no_transcript", "없음")
    s = env["run"]([item("aaaaaaa_aaa"), item("bbbbbbbbbbb"), item("ccccccccccc")])
    assert [v for v, _ in env["fetches"]] == ["ccccccccccc"] and s["done"] == 1


def test_gap_between_requests(env):
    env["summarize_error"]["aaaaaaaaaaa"] = SummaryError("format", "형식")
    env["script"]["bbbbbbbbbbb"] = err("no_transcript")
    env["run"]([item("aaaaaaaaaaa"), item("bbbbbbbbbbb"), item("ccccccccccc")])
    times = [t for _, t in env["fetches"]]
    # 요약이 30초 걸린 뒤에는 기다리지 않고, 요약 없이 바로 다음이면 이전 요청 종료부터 20초를 기다린다
    assert env["sleeps"] == [20]
    assert all(b - a >= backlog.MIN_GAP_SECONDS for a, b in zip(times, times[1:]))


def test_limit(env):
    env["run"]([item("aaaaaaaaaaa"), item("bbbbbbbbbbb"), item("ccccccccccc")], limit=2)
    assert len(env["fetches"]) == 2


def test_permanent_and_format_failures_recorded(env):
    env["script"]["aaaaaaaaaaa"] = err("unavailable", "Private video")
    env["summarize_error"]["bbbbbbbbbbb"] = SummaryError("format", "category: 목록에 없는 값")
    s = env["run"]([item("aaaaaaaaaaa", "비공개"), item("bbbbbbbbbbb"), item("ccccccccccc")])
    f = failed(env)
    assert [(r["video_id"], r["reason"]) for r in f] == [("aaaaaaaaaaa", "unavailable"), ("bbbbbbbbbbb", "format")]
    assert f[0]["title"] == "비공개" and f[0]["error"] == "Private video" and "T" in f[0]["time"]
    assert s["done"] == 1 and s["failed"] == 2 and s["stopped"] is None


def test_blocked_stops_run(env):
    env["script"]["bbbbbbbbbbb"] = err("blocked", "HTTP Error 429")
    s = env["run"]([item("aaaaaaaaaaa"), item("bbbbbbbbbbb"), item("ccccccccccc")])
    assert s["stopped"] == "blocked" and len(env["fetches"]) == 2 and failed(env) == []
    assert "blocked" in env["lines"][-2]


def test_claude_error_stops_run(env):
    env["summarize_error"]["aaaaaaaaaaa"] = SummaryError("transient", "claude -p 시간 초과")
    s = env["run"]([item("aaaaaaaaaaa"), item("bbbbbbbbbbb")])
    assert s["stopped"] == "claude" and len(env["fetches"]) == 1 and failed(env) == []


def test_transient_counted_then_failed_after_three(env):
    env["script"]["aaaaaaaaaaa"] = err("transient", "403")
    for n in (1, 2):
        s = env["run"]([item("aaaaaaaaaaa")])
        assert s["transient"] == 1 and env["state"].attempts() == {"aaaaaaaaaaa": n} and failed(env) == []
    s = env["run"]([item("aaaaaaaaaaa")])
    assert s["failed"] == 1 and env["state"].attempts() == {}
    assert failed(env)[0]["reason"] == "transient" and "3회" in failed(env)[0]["error"]
    env["run"]([item("aaaaaaaaaaa")])
    assert len(env["fetches"]) == 3


def test_success_clears_attempts(env):
    env["state"].add_attempt("aaaaaaaaaaa")
    env["run"]([item("aaaaaaaaaaa")])
    assert env["state"].attempts() == {}
