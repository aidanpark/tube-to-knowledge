"""영어 요약 · 주간 요약: 프롬프트 선택, 영어 열거 값 → 저장 코드, 본문 제목, 설치 기본값. claude · wrangler 는 호출하지 않는다."""
import json
import re
import subprocess

import pytest

from t2k import config, i18n, setup, summarize, weekly
from t2k.summarize import SummaryError, render, validate
from tests.test_summarize import TR

HANGUL = re.compile(r"[가-힣]")

FULL_EN = {
    "category": "0001_AI_개발도구", "categoryProposal": None,
    "summary": "The video explains a tool.", "shortSummary": "A tool explained.",
    "keyPoints": ["First point", "Second point"],
    "claims": [{"text": "It is fast", "evidence": "demo", "time": "1:05", "flag": False},
               {"text": "Everyone uses it", "evidence": "opinion", "time": "2:10", "flag": True}],
    "personalInsight": "Useful for automation.", "actionableItems": ["Try it"],
    "timestamps": [{"time": "0:00", "topic": "Intro"}, {"time": "1:05", "topic": "Demo"}],
    "tags": ["ai", "tools"], "lifespan": "medium", "quality": "high", "recommendation": "must-watch",
    "watchReason": "The demo matters.",
}


@pytest.fixture
def en(data):
    f = data / "config.toml"
    f.write_text(f.read_text().replace('language = "ko"', 'language = "en"'))
    config.reload()
    assert i18n.lang() == "en"
    return data


def test_english_prompt_files_have_no_korean():
    for name in ("summarize.txt", "weekly.txt"):
        text = (config.CODE / "prompts" / "en" / name).read_text()
        assert not HANGUL.search(text), name
        assert "UNTRUSTED" in text and "{{" in text


def test_english_prompt_selected(en):
    p = summarize.build_prompt({**TR, "chapters": ["[0:00] hello"], "short": False})
    assert p.startswith("You are a personal research assistant")
    assert "must-rewatch|must-watch|watch-if-time|summary-enough|skip" in p
    assert '"0099_기타"' in p  # 기타 카테고리의 키는 그 사람의 categories.toml 을 따른다
    assert "자동 생성" not in p and "일반 동영상" not in p and "(설명란 없음)" not in p


def test_korean_prompt_unchanged():
    p = summarize.build_prompt({**TR, "chapters": ["[0:00] 안녕"], "short": False})
    assert p.startswith("당신은 유튜브 영상의 자막을")
    assert '맞는 카테고리가 없을 때만 "0099_기타"를 쓰고' in p and "반복필수시청|필수시청" in p


def test_english_tokens_map_to_stored_codes(en):
    s = validate(json.loads(json.dumps(FULL_EN)), short=False)
    assert (s["recommendation"], s["rating"], s["lifespan"]) == ("필수시청", 4, "중간")
    assert [c["evidence"] for c in s["claims"]] == ["시연", "의견"]
    short = validate({**{k: FULL_EN[k] for k in ("category", "categoryProposal", "summary", "shortSummary", "lifespan",
                                                  "quality", "watchReason")},
                      "keyPoint": "One", "tags": ["a"], "recommendation": "skip"}, short=True)
    assert short["recommendation"] == "시청 불필요 삭제추천" and short["rating"] == 1
    music = validate({"kind": "music", "category": "0010_음악", "artist": "A", "song": "S", "format": "live",
                      "shortSummary": "A - S", "tags": ["pop"]}, short=False, music=True)
    assert music["format"] == "라이브"
    with pytest.raises(SummaryError):
        validate({**FULL_EN, "recommendation": "필수시청x"}, short=False)


def test_english_render_headings(en):
    s = validate(json.loads(json.dumps(FULL_EN)), short=False)
    md = render({**TR, "title": "Title", "author": "Channel"}, s, "2026-10-21")
    assert "**Rating** 4/5 Must watch · **Lifespan** Medium · **Quality** High" in md
    for h in ("## Summary", "## Key points", "## Claims and evidence", "## Personal insight", "## Timestamps"):
        assert h in md
    assert "- [Demo] It is fast" in md and "Needs checking" in md
    body = md.split("\n---\n", 1)[1]  # 앞머리에는 저장 코드(한국어)가 들어간다. 본문만 본다
    assert not HANGUL.search(body)


def test_korean_render_unchanged():
    from tests.test_summarize import FULL
    md = render(TR, validate(json.loads(json.dumps(FULL)), short=False), "2026-10-19")
    assert "## 요약" in md and "**평가** 4/5 필수시청" in md and "**품질** high" in md


def test_weekly_english_prompt_and_change_kinds(en):
    v = {"video_id": "vid00000001", "title": "T", "channel": "C", "short": False, "recommendation": "시간되면 시청",
         "lifespan": "긺", "shortSummary": "S", "keyPoints": ["k"], "claims": [{"text": "x", "flag": True}]}
    cat = config.categories()[0]
    p = weekly.build_prompt("2026-W42", cat, [v], {"week": "2026-W41", "headline": "H", "highlights": ["h"]})
    assert p.startswith("You are a personal research assistant that organizes")
    assert "Recommendation: watch-if-time · Lifespan: long" in p and "2026-10-12 (Mon) to 2026-10-18 (Sun)" in p
    assert '"new" for content' in p
    out = weekly.validate({"headline": "H", "highlights": ["a"],
                           "changes": [{"kind": "new", "text": "n"}, {"kind": "replaced", "text": "r"}, {"kind": "x", "text": "y"}]},
                          ["vid00000001"], set(), has_prev=True)
    assert [c["kind"] for c in out["changes"]] == ["새로움", "대체됨"]


def test_init_writes_language(data, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "new")
    monkeypatch.setattr(setup, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    setup.init(log=lambda *_: None)
    assert 'language = "en"' in (tmp_path / "new" / "config.toml").read_text()


@pytest.mark.parametrize("code,other,profile", [("en", 'name = "Other"', "# Interests"), ("ko", 'name = "기타"', "# 관심사")])
def test_user_add_defaults_follow_language(data, monkeypatch, code, other, profile):
    f = data / "config.toml"
    f.write_text(f.read_text().replace('language = "ko"', f'language = "{code}"'))
    config.reload()
    monkeypatch.setattr(setup, "create_kv", lambda uid: "0" * 32)
    monkeypatch.setattr(setup.deploy, "placeholder", lambda u: None)
    monkeypatch.setattr(setup, "_commit", lambda *a, **k: None)
    assert setup.add_user("carol", "Carol", "1" * 18, ["2" * 18], log=lambda *_: None) == 0
    folder = data / "users" / "carol"
    assert other in (folder / "categories.toml").read_text()
    assert (folder / "profile.md").read_text().startswith(profile)
