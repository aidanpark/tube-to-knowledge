"""yt-dlp 자막 선택 · 재시도 · 업데이트 시험. yt-dlp · GitHub · brew 는 호출하지 않는다."""
import json

import pytest

from t2k import transcript, ytdlp_update
from t2k.transcript import TranscriptError, classify, parse_json3, pick_track

INFO = {
    "subtitles": {"en-eEY6OEpapPo": [], "ko-tgb1yGiJu6Y": [], "live_chat": []},
    "automatic_captions": {"en": [], "ko": [], "ko-orig": []},
    "language": "ko",
}
OK = ({"title": "t", "author": "a", "duration": 103, "published": "2026-09-19"},
      [{"start": 0.0, "text": "안녕"}, {"start": 31.0, "text": "다음"}], "ko", False)


def test_pick_manual_in_language_order():
    assert pick_track(INFO, ["ko", "en"]) == ("ko-tgb1yGiJu6Y", False)
    assert pick_track(INFO, ["en", "ko"]) == ("en-eEY6OEpapPo", False)


def test_pick_original_auto_not_translation():
    d = {"subtitles": {}, "automatic_captions": {"ko": [], "en": [], "en-orig": []}, "language": "en"}
    assert pick_track(d, ["ko", "en"]) == ("en-orig", True)
    d = {"subtitles": {}, "automatic_captions": {"ko": [], "ja": []}, "language": "ja"}
    assert pick_track(d, ["ko"]) == ("ja", True)


def test_pick_none():
    assert pick_track({"subtitles": {"live_chat": []}, "automatic_captions": {}}, ["ko"]) is None


def test_parse_json3_skips_empty_events():
    text = json.dumps({"events": [{"tStartMs": 0, "segs": [{"utf8": "안녕"}, {"utf8": "하세요"}]},
                                  {"tStartMs": 1500, "segs": [{"utf8": "\n"}]},
                                  {"tStartMs": 3000}]})
    assert parse_json3(text) == [{"start": 0.0, "text": "안녕하세요"}]


@pytest.mark.parametrize("err,reason", [
    ("ERROR: HTTP Error 429: Too Many Requests", "blocked"),
    ("ERROR: Sign in to confirm you're not a bot", "blocked"),
    ("ERROR: [youtube] x: Private video", "unavailable"),
    ("ERROR: [youtube] x: Video unavailable", "unavailable"),
    ("ERROR: HTTP Error 403: Forbidden", "transient"),
])
def test_classify(err, reason):
    assert classify(err) == reason


@pytest.fixture
def calls(monkeypatch):
    """_attempt 를 대본대로 동작시키고, 대기 · 업데이트 호출을 기록한다."""
    rec = {"attempts": 0, "sleeps": [], "updates": 0, "script": [], "updated": False}

    def attempt(vid, langs):
        rec["attempts"] += 1
        r = rec["script"].pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def update():
        rec["updates"] += 1
        return rec["updated"]

    monkeypatch.setattr(transcript, "_attempt", attempt)
    monkeypatch.setattr(ytdlp_update, "maybe_update", update)
    return rec


def run(rec):
    return transcript.fetch("abcdefghijk", sleep=rec["sleeps"].append)


def test_success_after_retry(calls):
    calls["script"] = [TranscriptError("transient", "x"), OK]
    r = run(calls)
    assert r["status"] == "ok" and r["short"] and r["url"].endswith("/shorts/abcdefghijk")
    assert calls["sleeps"] == [30] and calls["updates"] == 0 and r["chapters"] == ["[0:00] 안녕 다음"]


def test_three_failures_then_update_and_retry(calls):
    calls["script"] = [TranscriptError("transient", "403")] * 3 + [OK]
    calls["updated"] = True
    assert run(calls)["status"] == "ok"
    assert calls["attempts"] == 4 and calls["sleeps"] == [30, 120] and calls["updates"] == 1


def test_blocked_returns_immediately(calls):
    calls["script"] = [TranscriptError("blocked", "429"), OK]
    r = run(calls)
    assert r["status"] == "error" and r["reason"] == "blocked"
    assert calls["attempts"] == 1 and calls["sleeps"] == [] and calls["updates"] == 0


def test_blocked_after_transient_stops_retrying(calls):
    calls["script"] = [TranscriptError("transient", "403"), TranscriptError("blocked", "429"), OK]
    r = run(calls)
    assert r["reason"] == "blocked" and calls["attempts"] == 2 and calls["updates"] == 0


def test_three_failures_no_update(calls):
    calls["script"] = [TranscriptError("transient", "403")] * 3
    r = run(calls)
    assert r["status"] == "error" and r["reason"] == "transient" and calls["attempts"] == 3 and calls["updates"] == 1


def test_permanent_failure_no_retry(calls):
    calls["script"] = [TranscriptError("no_transcript", "없음")]
    r = run(calls)
    assert r["reason"] == "no_transcript" and calls["attempts"] == 1 and calls["updates"] == 0


@pytest.fixture
def upd(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp_update.config, "path", lambda name: tmp_path / name)
    rec = {"versions": ["2026.08.19"], "latest": "2026.08.19", "brew": 0}
    monkeypatch.setattr(ytdlp_update, "installed_version", lambda: rec["versions"].pop(0) if len(rec["versions"]) > 1 else rec["versions"][0])
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: rec["latest"])

    class P:
        returncode = 0

    def brew(*a, **k):
        rec["brew"] += 1
        return P()

    monkeypatch.setattr(ytdlp_update.subprocess, "run", brew)
    return rec


def test_update_when_newer(upd):
    upd["versions"] = ["2026.08.19", "2026.10.02"]
    upd["latest"] = "2026.10.02"
    assert ytdlp_update.maybe_update(now=1e9) is True and upd["brew"] == 1


def test_no_update_when_latest(upd):
    assert ytdlp_update.maybe_update(now=1e9) is False and upd["brew"] == 0


def test_update_check_throttled(upd):
    upd["latest"] = "2026.10.02"
    ytdlp_update.maybe_update(now=1e9)
    assert ytdlp_update.maybe_update(now=1e9 + 3600) is False and upd["brew"] == 1


def test_brew_not_yet_released(upd):
    upd["latest"] = "2026.10.02"  # GitHub 에는 있으나 Homebrew 미반영: 판이 바뀌지 않음
    assert ytdlp_update.maybe_update(now=1e9) is False and upd["brew"] == 1


def test_version_key():
    assert ytdlp_update.version_key("2026.8.19") == ytdlp_update.version_key("2026.08.19")
    assert ytdlp_update.version_key("2026.08.19.1") > ytdlp_update.version_key("2026.08.19")
