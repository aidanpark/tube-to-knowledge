"""음악 형식: 노래 · 공연 · 뮤직비디오는 곡 정보만 정리한다. yt-dlp · claude 는 호출하지 않는다."""
import json
from datetime import datetime

import pytest

from ytbook import site, summarize, transcript
from ytbook.summarize import KST, SummaryError, build_prompt, render, validate
from tests.test_summarize import TR

MUSIC = {"kind": "music", "category": "0010_음악", "categoryProposal": None, "artist": "이승윤", "song": "솔로",
         "songs": [], "album": "꿈의 거처", "releaseYear": 2021, "format": "라이브",
         "credits": ["작사 · 작곡 이승윤"], "shortSummary": "이승윤 - 솔로 (꿈의 거처, 2021)", "tags": ["인디", "라이브"]}


def music_tr(**kw):
    return {**TR, "categories": ["Music"], "music": {}, "chapters": ["[0:00] 가사"], **kw}


def test_music_of_uses_track_info_only_with_track():
    assert transcript.music_of({"release_year": 2020, "categories": ["Music"]}) == {}  # 곡명 없으면 영상 연도일 뿐
    m = transcript.music_of({"track": "솔로", "artists": ["이승윤"], "album": "꿈의 거처", "release_year": 2021})
    assert m == {"track": "솔로", "artist": "이승윤", "album": "꿈의 거처", "release_year": 2021}


def test_prompt_offers_music_schema_only_for_music_category():
    assert '"kind": "music"' not in build_prompt({**TR, "chapters": ["[0:00] 안녕"]})
    p = build_prompt(music_tr(music={"track": "솔로", "artist": "이승윤"}))
    assert '"kind": "music"' in p and "기본 형식" in p and "곡명 솔로 · 가수 이승윤" in p
    p = build_prompt(music_tr(chapters=[]))  # 자막 없는 음악 영상은 음악 형식만
    assert "자막이 없는 음악 영상" in p and "기본 형식" not in p and "(자막 없음)" in p


def test_validate_music():
    s = validate(json.loads(json.dumps(MUSIC)), short=False, music=True)
    assert s["kind"] == "music" and s["releaseYear"] == "2021" and s["format"] == "라이브" and "rating" not in s
    bad = {**MUSIC, "releaseYear": "2021년쯤", "format": "모름"}
    s = validate(bad, short=False, music=True)
    assert s["releaseYear"] is None and s["format"] == "기타"
    with pytest.raises(SummaryError):
        validate({**MUSIC, "artist": ""}, short=False, music=True)
    with pytest.raises(SummaryError):  # 음악 후보가 아니면 음악 형식을 받지 않는다 (기본 형식 검증)
        validate(json.loads(json.dumps(MUSIC)), short=False, music=False)


def test_render_and_site_page(tmp_path):
    s = validate(json.loads(json.dumps(MUSIC)), short=False, music=True)
    md = render(music_tr(), s, "2026-10-20")
    assert "## 곡 정보" in md and "**앨범** 꿈의 거처" in md and "## 요약" not in md
    src, out, state = tmp_path / "summaries", tmp_path / "site", tmp_path / "state"
    (src / "0010_음악").mkdir(parents=True)
    (src / "0010_음악" / f"2026-10-20_{TR['video_id']}.md").write_text(md)
    state.mkdir()
    site.build(src, out, state, datetime(2026, 10, 21, 9, tzinfo=KST))
    page = (out / f"v/{TR['video_id']}.html").read_text()
    assert "곡 정보" not in page and "꿈의 거처" in page and "♪ 라이브" in page and "작사 · 작곡 이승윤" in page
    assert "♪ 라이브" in (out / "index.html").read_text()  # 목록에는 별점 대신 형식
    assert "이승윤" in (out / "search.json").read_text()


def test_music_without_subtitles_is_not_failed(monkeypatch, tmp_path):
    d = {"title": "곡", "channel": "가수", "duration": 200, "upload_date": "20240101", "categories": ["Music"],
         "track": "곡", "artist": "가수", "subtitles": {}, "automatic_captions": {}}
    monkeypatch.setattr(transcript, "info", lambda vid, w: d)
    meta, segs, lang, gen = transcript._attempt("abcdefghijk", ["ko"])
    assert segs == [] and meta["music"]["track"] == "곡"
    with pytest.raises(transcript.TranscriptError):
        monkeypatch.setattr(transcript, "info", lambda vid, w: {**d, "categories": ["Education"]})
        transcript._attempt("abcdefghijk", ["ko"])
