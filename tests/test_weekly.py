"""주간 요약: 주의 경계, 대상 카테고리, 형식 검증, 파일 생성, 사이트 연결, 파이프라인 단계. claude 는 호출하지 않는다."""
import json
from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from t2k import config, pipeline, site, weekly
from t2k.summarize import SummaryError, validate as validate_summary
from tests.test_site import write
from tests.test_summarize import FULL, SHORT, TR

KST = ZoneInfo("Asia/Seoul")  # 시험의 고정 시각용. 프로그램은 이 맥의 현지 시간대를 쓴다

ALICE = lambda: config.user("alice")


def add(src, vid, cat="0001_AI_개발도구", day="2026-10-14", short=False, music=False):
    if music:
        s = {"kind": "music", "category": cat, "categoryProposal": None, "artist": "가수", "song": "곡", "songs": [],
             "album": None, "releaseYear": None, "format": "음원", "credits": [], "shortSummary": "가수 - 곡", "tags": ["노래"], "links": []}
    elif short:
        s = validate_summary({**SHORT, "category": cat}, short=True)
    else:
        s = validate_summary(json.loads(json.dumps({**FULL, "category": cat})), short=False)
    tr = {**TR, "video_id": vid, "title": f"제목 {vid}", "short": short}
    write(src, tr, s, day)


def reply(**over):
    d = {"headline": "이번 주 AI 개발 도구", "highlights": ["핵심 하나", "핵심 둘"],
         "merged": [{"title": "같은 소식", "summary": "두 영상이 같은 소식을 다룬다.", "videos": ["vid00000001", "vid00000002"]}],
         "shorts": [], "changes": [{"kind": "새로움", "text": "새 도구가 나왔다."}],
         "picks": [{"id": "vid00000001", "reason": "직접 볼 만하다."}]}
    d.update(over)
    return json.dumps(d, ensure_ascii=False)


def test_week_boundaries():
    assert weekly.last_week(datetime(2026, 10, 19, 0, 0, tzinfo=KST)) == "2026-W42"  # 월요일 0시 = 일요일 24:00
    assert weekly.last_week(datetime(2026, 10, 18, 23, 59, tzinfo=KST)) == "2026-W41"
    assert weekly.week_dates("2026-W42") == (datetime(2026, 10, 12).date(), datetime(2026, 10, 18).date())
    assert weekly.week_key(datetime(2027, 1, 1).date()) == "2026-W53"  # ISO 주: 연도가 바뀌어도 주를 따른다


def test_candidates_need_min_items_and_skip_music(data):
    with config.as_user(ALICE()):
        src = config.path("summaries")
        add(src, "vid00000001", day="2026-10-12")
        add(src, "vid00000002", day="2026-10-18")
        add(src, "vid00000003", day="2026-10-19")  # 다음 주
        add(src, "vid00000004", cat="0002_AI_뉴스")  # 한 편뿐
        add(src, "vid00000005", cat="0010_음악", music=True)
        add(src, "vid00000006", cat="0010_음악", music=True)
        jobs = weekly.candidates("2026-W42")
    assert [(c.id, [v["video_id"] for v in vids]) for c, vids in jobs] == [("0001", ["vid00000001", "vid00000002"])]


def test_validate_drops_bad_parts():
    ids = ["vid00000001", "vid00000002"]
    d = json.loads(reply(
        merged=[{"title": "하나뿐", "summary": "요약", "videos": ["vid00000001"]},           # 2편 미만
                {"title": "없는 영상", "summary": "요약", "videos": ["vid00000001", "zzzzzzzzzzz"]}],
        shorts=[{"title": "Shorts 아님", "summary": "요약", "videos": ids}],             # Shorts 가 아니다
        changes=[{"kind": "대체됨", "text": "지난주와 다르다."}, {"kind": "기타", "text": "x"}],
        picks=[{"id": "zzzzzzzzzzz", "reason": "없는 영상"}, {"id": "vid00000002", "reason": "좋다."}]))
    out = weekly.validate(d, ids, set(), has_prev=False)
    assert out["merged"] == [] and out["shorts"] == [] and out["changes"] == []  # 지난 주간 요약이 없으면 변경도 없다
    assert out["picks"] == [{"id": "vid00000002", "reason": "좋다."}]
    with pytest.raises(SummaryError):
        weekly.validate({"headline": "", "highlights": ["a"]}, ids, set(), False)


def test_generate_writes_doc_and_links_previous(data, monkeypatch):
    prompts = []
    monkeypatch.setattr(weekly, "call_claude", lambda p, m=None: prompts.append(p) or reply())
    with config.as_user(ALICE()):
        src = config.path("summaries")
        for vid, day in (("vid00000001", "2026-10-06"), ("vid00000002", "2026-10-07"),
                         ("vid00000001", "2026-10-13"), ("vid00000002", "2026-10-14")):
            add(src, vid, day=day)
        items = site.load_summaries(src)
        # 같은 영상을 다시 요약하면 이전 파일은 남지 않는다: 41주는 직접 만든다
        (cat, vids), = weekly.candidates("2026-W42", items)
        p41 = weekly.doc_path("2026-W41", "0001")
        p41.parent.mkdir(parents=True)
        p41.write_text(json.dumps({"week": "2026-W41", "cat_id": "0001", "headline": "지난주", "highlights": ["옛 핵심"]}))
        path = weekly.generate("2026-W42", cat, vids)
        doc = json.loads(path.read_text())
        assert path == config.path("weekly") / "2026-W42" / "0001.json"
        assert doc["previous"] == "2026-W41" and doc["range"] == ["2026-10-12", "2026-10-18"]
        assert [v["id"] for v in doc["videos"]] == ["vid00000001", "vid00000002"] and doc["changes"]
        assert "옛 핵심" in prompts[0] and "---BEGIN UNTRUSTED SUMMARIES---" in prompts[0]
        assert weekly.candidates("2026-W42") == []  # 이미 만들었다


def test_format_errors_count_attempts(data, monkeypatch):
    monkeypatch.setattr(weekly, "call_claude", lambda p, m=None: "형식이 아님")
    with config.as_user(ALICE()):
        src = config.path("summaries")
        add(src, "vid00000001")
        add(src, "vid00000002")
        for _ in range(3):
            (cat, vids), = weekly.candidates("2026-W42")
            with pytest.raises(SummaryError):
                weekly.generate("2026-W42", cat, vids)
        assert weekly.candidates("2026-W42") == []  # 3회 실패 후 건너뛴다


def test_site_pages(data, monkeypatch, tmp_path):
    monkeypatch.setattr(weekly, "call_claude", lambda p, m=None: reply(headline="<b>주간</b> 제목"))
    with config.as_user(ALICE()):
        src = config.path("summaries")
        add(src, "vid00000001", day="2026-10-13")
        add(src, "vid00000002", day="2026-10-14")
        (cat, vids), = weekly.candidates("2026-W42")
        weekly.generate("2026-W42", cat, vids)
        out = site.build(src, tmp_path / "site", tmp_path / "state", datetime(2026, 10, 21, 9, 0, tzinfo=KST))
    page = (out / "w" / "2026-W42" / "0001.html").read_text()
    assert "&lt;b&gt;주간&lt;/b&gt; 제목" in page and "<b>주간</b>" not in page  # 모델이 생성한 글은 이스케이프한다
    assert "겹친 소식 통합" in page and 'href="../../v/vid00000001.html"' in page and "이번 주 요약 2편" in page
    assert "2026년 42주" in (out / "weeks.html").read_text()
    assert "주간 요약 1편이 준비되었습니다" in (out / "index.html").read_text()  # 43주 홈: 지난주 주간 요약
    assert 'href="../w/2026-W42/0001.html"' in (out / "v" / "vid00000001.html").read_text()
    assert "w/2026-W42/0001.html" in (out / "c" / "0001.html").read_text()


def test_pipeline_step_once_per_cycle_then_done(data, monkeypatch):
    monkeypatch.setattr(weekly, "call_claude", lambda p, m=None: reply())
    with config.as_user(ALICE()):
        src = config.path("summaries")
        for vid in ("vid00000001", "vid00000002"):
            add(src, vid)
            add(src, vid.replace("0000000", "1111111"), cat="0002_AI_뉴스")
    status = pipeline.Status(data / "state.json")
    now = datetime(2026, 10, 19, 0, 1).astimezone().timestamp()  # 이 맥의 현지 시각 월요일 0시 1분
    users = config.users()
    made = [pipeline.weekly_step(status, users, now) for _ in range(3)]
    assert [m[1] for m in made[:2]] == ["주간 2026-W42 AI_개발도구", "주간 2026-W42 AI_뉴스"] and made[2] is None
    assert status.d["weekly_done"] == "2026-W42"
    monkeypatch.setattr(weekly, "candidates", lambda key: pytest.fail("다음 주까지 다시 확인하지 않는다"))
    assert pipeline.weekly_step(status, users, now) is None
