"""사이트 생성 시험. 요약 파일을 임시 폴더에 만들고 site.build 결과를 확인한다."""
import json
from datetime import datetime

import pytest

from ytbook import site
from ytbook.summarize import KST, render, validate
from tests.test_summarize import FULL, SHORT, TR

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=KST)  # 수요일, 43주


def write(src, tr, s, summarized):
    d = src / s["category"]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{summarized}_{tr['video_id']}.md").write_text(render(tr, s, summarized))


@pytest.fixture
def built(tmp_path):
    src, out, state = tmp_path / "summaries", tmp_path / "site", tmp_path / "state"
    full = validate(json.loads(json.dumps(FULL)), short=False)
    full["title"] = "<script>alert(1)</script>"  # 모델이 생성한 값은 이스케이프되어야 한다
    write(src, {**TR, "title": "<script>alert(1)</script>"}, full, "2026-10-19")
    write(src, {**TR, "video_id": "shortshort1", "short": True, "duration": 50, "duration_human": "0:50",
                "url": "https://www.youtube.com/shorts/shortshort1"}, validate(SHORT, short=True), "2026-10-12")
    state.mkdir()
    (state / "failed.jsonl").write_text('{"video_id": "failedvid01"}\n{"video_id": "failedvid01"}\n{"video_id": "failedvid02"}\n')
    site.build(src, out, state, NOW)
    return out


def test_pages_exist(built):
    for p in ["index.html", "categories.html", "weeks.html", "search.html", "search.json",
              "assets/style.css", "v/abcdefghijk.html", "v/shortshort1.html", "c/0001.html", "c/0099.html"]:
        assert (built / p).exists(), p


def test_blocks_robots(built):
    assert (built / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n"
    assert "X-Robots-Tag: noindex" in (built / "_headers").read_text()
    assert 'name="robots" content="noindex' in (built / "index.html").read_text()


def test_escapes_model_text(built):
    html = (built / "v/abcdefghijk.html").read_text()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_summary_page_links(built):
    html = (built / "v/abcdefghijk.html").read_text()
    assert 'href="https://www.youtube.com/watch?v=abcdefghijk&amp;t=161s"' in html  # 주장 시점
    assert 'href="../c/0001.html"' in html and 'href="../assets/style.css' in html
    assert "확인 필요" in html and "주장과 근거" in html


def test_short_page(built):
    html = (built / "v/shortshort1.html").read_text()
    assert "youtube.com/shorts/shortshort1" in html and "핵심" in html and "타임스탬프" not in html


def test_home_counts_this_week_and_failed(built):
    html = (built / "index.html").read_text()
    assert "43주 (10/19–10/25)" in html and "이번 주 요약 1편" in html
    assert "요약에 실패한 동영상 2편" in html and 'href="failed.html"' in html
    failed = (built / "failed.html").read_text()
    assert failed.count('class="vrow"') == 2


def test_search_index(built):
    data = json.loads((built / "search.json").read_text())
    ids = [i["id"] for i in data["items"]]
    assert ids == ["abcdefghijk", "shortshort1"]  # 최신순
    short = data["items"][1]
    assert short["short"] is True and short["href"] == "v/shortshort1.html" and "핵심 하나." in short["text"]


def test_rebuild_replaces_old_files(built, tmp_path):
    (built / "stale.html").write_text("x")
    site.build(tmp_path / "summaries", built, tmp_path / "state", NOW)
    assert not (built / "stale.html").exists() and (built / "index.html").exists()


def test_empty_site(tmp_path):
    out = site.build(tmp_path / "none", tmp_path / "site", tmp_path / "state", NOW)
    assert "아직 요약이 없습니다" in (out / "index.html").read_text()


def test_status_notices(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "status.json").write_text(json.dumps({"blocked_since": NOW.timestamp() - 5 * 3600}))
    out = site.build(tmp_path / "none", tmp_path / "site", state, NOW)
    html = (out / "index.html").read_text()
    assert "5시간째" in html


def test_proposal_href():
    href = site.proposal_href("0011_역술·운세: 사주, 타로 등")
    assert href.startswith("categories.html?") and href.endswith("#new") and "name=%EC%97%AD" in href
    assert site.proposal_href(None) is None and site.proposal_href("새 카테고리가 필요함") is None


def test_manage_widgets(built):
    page = (built / "v/abcdefghijk.html").read_text()
    assert "data-manage" in page and "data-move-to" in page
    assert "data-cat-new" in (built / "categories.html").read_text()
    assert "data-cat-delete" in (built / "c/0001.html").read_text()
    assert "data-cat-delete" not in (built / "c/0099.html").read_text()  # 기타는 삭제할 수 없다
