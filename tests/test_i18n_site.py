"""사이트 화면 언어: [general] language = "en" 이면 화면 문구가 영어다. 저장된 값(data-*)은 그대로 두어 필터가 동작한다."""
import json
from datetime import datetime

import pytest

from t2k import config, site, weekly
from t2k.summarize import KST, validate
from tests import conftest
from tests.test_site import write
from tests.test_summarize import FULL, SHORT, TR

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=KST)  # 43주


@pytest.fixture
def en_site(data, tmp_path):
    (data / "config.toml").write_text(conftest.CONFIG.replace('language = "ko"', 'language = "en"'))
    config.reload()
    with config.as_user(config.user("alice")):
        src = config.path("summaries")
        write(src, TR, validate(json.loads(json.dumps(FULL)), short=False), "2026-10-13")
        write(src, {**TR, "video_id": "bbbbbbbbbbb"}, validate(json.loads(json.dumps(FULL)), short=False), "2026-10-14")
        write(src, {**TR, "video_id": "shortshort1", "short": True, "duration": 50, "duration_human": "0:50",
                    "url": "https://www.youtube.com/shorts/shortshort1"}, validate(SHORT, short=True), "2026-10-19")
        wk = config.path("weekly") / "2026-W42"
        wk.mkdir(parents=True)
        (wk / "0001.json").write_text(json.dumps({
            "week": "2026-W42", "category": "0001_AI_개발도구", "cat_id": "0001", "cat_name": "AI_개발도구",
            "range": ["2026-10-12", "2026-10-18"], "previous": "2026-W41", "generated": "",
            "videos": [{"id": "abcdefghijk", "title": "t", "short": False, "rating": 4, "duration": "1:00"},
                       {"id": "bbbbbbbbbbb", "title": "t", "short": False, "rating": 4, "duration": "1:00"}],
            "headline": "Headline", "highlights": ["One"], "merged": [], "shorts": [],
            "changes": [{"kind": "대체됨", "text": "x"}], "picks": []}))
        state = tmp_path / "state"
        state.mkdir()
        (state / "failed.jsonl").write_text('{"video_id": "failedvid01", "reason": "no_transcript"}\n')
        return site.build(src, tmp_path / "site", state, NOW)


def test_english_chrome(en_site):
    home = (en_site / "index.html").read_text()
    assert '<html lang="en"' in home and "<title>Video Summaries</title>" in home
    assert "This week · Week 43 (10/19–10/25)" in home and "Your weekly summary is ready" in home
    assert "1 video couldn&#39;t be summarized" in home and "Recent summaries" in home
    assert "Lasts months" in home and 'data-life="중간"' in home  # 표시는 영어, 필터 값은 저장된 값
    for ko in ("최근 요약", "주간 요약", "수명", "이번 주", "카테고리 보기"):
        assert ko not in home, ko

    page = (en_site / "v" / "abcdefghijk.html").read_text()
    for en in ("Claims &amp; evidence", "Needs checking", "Must watch", "Quality: High", "Personal insight", "Timestamps",
               "Delete this summary", "Included in the weekly summary"):
        assert en in page, en
    for ko in ("주장과 근거", "확인 필요", "필수시청", "품질", "타임스탬프", "이 요약 삭제"):
        assert ko not in page, ko

    w = (en_site / "w" / "2026-W42" / "0001.html").read_text()
    assert "Weekly summary · Week 42, 2026" in w and "10/12(Mon) – 10/18(Sun) · Based on 2 summaries" in w
    assert "Compared with the Week 41, 2026 summary." in w and ">Replaced<" in w

    assert "No captions" in (en_site / "failed.html").read_text()
    js = (en_site / "search.html").read_text()
    assert '"order_newest": "Newest"' in js and '"life.\\uae3a": "Lasts years"' in js  # 스크립트 문구 (tojson 은 한글을 \u 로 쓴다)


def test_korean_is_default_for_tests(data, tmp_path):
    out = site.build(tmp_path / "summaries", tmp_path / "site", tmp_path / "state", NOW)
    home = (out / "index.html").read_text()
    assert '<html lang="ko"' in home and "아직 요약이 없습니다" in home
