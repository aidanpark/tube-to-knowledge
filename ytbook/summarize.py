"""자막을 claude -p 로 요약하고, 검증한 뒤 요약 파일(Markdown)로 저장한다.

claude -p 는 빈 임시 디렉터리에서, 도구를 모두 끄고(--tools ""), CLAUDE.md · 훅 · MCP 를 끈 상태(--safe-mode)로 호출한다.
자막 안에 지시가 섞여 있어도 아무 동작으로 이어지지 않게 하기 위해서다.

요약 파일: summaries/<카테고리>/<요약일>_<video_id>.md
- 앞머리(--- … ---)의 각 줄은 `키: <JSON 값>` 이다. 사이트 생성은 앞머리만 읽는다.
- 본문은 사람이 읽는 Markdown 이다.
"""
import json
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import config

KST = ZoneInfo("Asia/Seoul")
RECOMMENDATIONS = {"반복필수시청": 5, "필수시청": 4, "시간되면 시청": 3, "요약으로 충분": 2, "시청 불필요 삭제추천": 1}
LIFESPANS = ("짧음", "중간", "긺")
QUALITIES = ("high", "medium", "low")
EVIDENCE = ("연구 인용", "데이터", "시연", "의견", "일화")
TIME_RE = re.compile(r"^(\d+:)?\d{1,2}:\d{2}$")
URL_RE = re.compile(r"https?://[^\s<>\"'\[\]（）「」『』【】〈〉《》]+")
MAX_LINKS = 5

SCHEMA_FULL = """{
  "category": "카테고리 목록의 값 하나",
  "categoryProposal": null,
  "summary": "영상이 말하는 내용만 3~5문장으로 요약한다. 관심사와의 연결은 쓰지 않는다",
  "shortSummary": "목록에 표시할 1문장 요약, 80자 이내",
  "keyPoints": ["핵심 포인트 3~7개"],
  "claims": [{"text": "영상의 핵심 주장", "evidence": "연구 인용|데이터|시연|의견|일화", "time": "m:ss", "flag": false}],
  "personalInsight": "관심사에 주는 실질적 시사점 1~2문장. 관심사와의 연결은 여기에만 쓴다. 관련이 없으면 null",
  "actionableItems": ["바로 적용할 수 있는 구체적 행동. 없으면 빈 배열"],
  "timestamps": [{"time": "m:ss", "topic": "주제 전환점 5~10개"}],
  "tags": ["태그 3~5개"],
  "lifespan": "짧음|중간|긺",
  "quality": "high|medium|low",
  "recommendation": "반복필수시청|필수시청|시간되면 시청|요약으로 충분|시청 불필요 삭제추천",
  "watchReason": "시청 권유 판단 근거 1~2문장"
}
claims 는 2~6개.
links: 설명란 링크 목록에서 영상 내용과 직접 관련된 자료(저장소, 도구, 문서, 논문, 원 게시물)만 골라 [{"ref": 번호, "label": "무엇의 링크인지 30자 이내"}] 로 0~5개 쓴다. 채널 · SNS · 후원 · 제휴 · 멤버십 · 강의 판매 링크는 넣지 않는다. 고를 것이 없으면 빈 배열 [] 이다. URL 자체는 쓰지 않고 번호만 쓴다."""

SCHEMA_SHORT = """{
  "category": "카테고리 목록의 값 하나",
  "categoryProposal": null,
  "summary": "영상이 말하는 내용만 1~2문장으로 요약한다. 관심사와의 연결은 쓰지 않는다",
  "shortSummary": "목록에 표시할 1문장 요약, 80자 이내",
  "keyPoint": "가장 중요한 핵심 한 가지",
  "tags": ["태그 2~4개"],
  "lifespan": "짧음|중간|긺",
  "quality": "high|medium|low",
  "recommendation": "반복필수시청|필수시청|시간되면 시청|요약으로 충분|시청 불필요 삭제추천",
  "watchReason": "시청 권유 판단 근거 1문장"
}
links: 설명란 링크 목록에서 영상 내용과 직접 관련된 자료(저장소, 도구, 문서, 논문, 원 게시물)만 골라 [{"ref": 번호, "label": "무엇의 링크인지 30자 이내"}] 로 0~5개 쓴다. 채널 · SNS · 후원 · 제휴 · 멤버십 · 강의 판매 링크는 넣지 않는다. 고를 것이 없으면 빈 배열 [] 이다. URL 자체는 쓰지 않고 번호만 쓴다."""


# 음악 형식: 노래 · 공연 · 뮤직비디오는 내용을 요약하지 않고 곡 정보만 정리한다
MUSIC_FORMATS = ("뮤직비디오", "라이브", "음원", "커버", "기타")
SCHEMA_MUSIC = """{
  "kind": "music",
  "category": "카테고리 목록의 값 하나",
  "categoryProposal": null,
  "artist": "가수 · 연주자 · 그룹",
  "song": "곡명. 여러 곡이면 대표 곡 또는 '메들리' 처럼 쓴다",
  "songs": ["여러 곡을 부르거나 연주하면 곡명 목록. 한 곡이면 빈 배열"],
  "album": "앨범 이름. 알 수 없으면 null",
  "releaseYear": "곡(앨범)의 발매년도 4자리. 알 수 없으면 null",
  "format": "뮤직비디오|라이브|음원|커버|기타",
  "credits": ["작사 · 작곡 · 편곡 · 연주자 · 공연 이름처럼 설명란에 적힌 정보. 없으면 빈 배열"],
  "shortSummary": "목록에 표시할 한 줄. 예: 가수 - 곡명 (앨범, 연도)",
  "tags": ["태그 1~4개. 장르 · 가수 · 공연 이름 등"]
}
음악 형식 규칙: 곡의 내용 · 가사를 요약하거나 옮겨 적지 않는다. 정보는 아래 「YouTube 음악 정보」 · 제목 · 채널 · 설명란에서만
가져오고, 거기에 없는 앨범 · 발매년도는 추측하지 않고 null 로 둔다. 영상의 게시일은 발매년도가 아니다."""


def music_candidate(tr: dict) -> bool:
    return "Music" in (tr.get("categories") or [])


def music_schema(tr: dict, base: str) -> str:
    """음악 후보의 형식 안내. 자막이 없으면 음악 형식만, 있으면 영상 성격에 따라 고르게 한다."""
    m = tr.get("music") or {}
    names = {"track": "곡명", "artist": "가수", "album": "앨범", "release_year": "발매년도"}
    info = " · ".join(f"{names[k]} {v}" for k, v in m.items()) or "(없음)"
    head = f"YouTube 음악 정보: {info}\n\n"
    if not tr.get("chapters"):
        return head + "이 영상은 자막이 없는 음악 영상이다. 아래 음악 형식으로 쓴다.\n" + SCHEMA_MUSIC
    return (head + "이 영상은 YouTube 분류가 음악이다. 노래 · 연주 · 공연 · 뮤직비디오처럼 음악 자체가 내용이면 "
            "음악 형식으로, 음악에 관한 정보성 영상(인터뷰 · 강의 · 해설 · 리뷰)이면 기본 형식으로 쓴다.\n\n"
            "음악 형식:\n" + SCHEMA_MUSIC + "\n\n기본 형식:\n" + base)


class SummaryError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason  # format (형식 오류, 재시도) · transient (일시 오류, 재시도)


def description_links(description: str) -> list[str]:
    """설명란의 URL 을 등장 순서대로, 중복 없이 반환한다 (최대 30개)."""
    out = []
    for u in URL_RE.findall(description or ""):
        u = u.rstrip(".,;:!?…'\"")
        while u.endswith(")") and u.count(")") > u.count("("):  # 괄호 짝이 맞는 위키 주소는 그대로 둔다
            u = u[:-1].rstrip(".,;:!?…'\"")
        if u not in out:
            out.append(u)
    return out[:30]


SHORTENERS = {"bit.ly", "a.co", "amzn.to", "naver.me", "t.co", "goo.gl", "tinyurl.com", "han.gl", "buly.kr",
              "me2.do", "url.kr", "lnkd.in", "rebrand.ly", "cutt.ly", "shorturl.at", "vo.la", "abit.ly", "ow.ly", "is.gd"}


def resolve_short(url: str) -> str:
    """단축 주소는 최종 주소로 바꾼다 (화면에 목적지 호스트를 보이기 위해서). 실패하면 원래 주소를 쓴다."""
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if host not in SHORTENERS:
        return url
    try:
        r = requests.head(url, allow_redirects=True, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        final = r.url
    except requests.RequestException:
        return url
    return final if final.startswith(("https://", "http://")) and urlsplit(final).netloc.lower() not in SHORTENERS else url


def resolve_links(links: list[dict]) -> list[dict]:
    return [{**x, "url": resolve_short(x["url"])} for x in links]


def _schema(tr: dict) -> str:
    base = SCHEMA_SHORT if tr["short"] else SCHEMA_FULL
    return music_schema(tr, base) if music_candidate(tr) else base


def build_prompt(tr: dict) -> str:
    cats = "\n".join(f"- {c.key}: {c.scope}" for c in config.categories())
    tpl = (config.CODE / "prompts" / "summarize.txt").read_text()
    user = config.current_user() or config.default_user()  # as_user 밖(수동 실행)에서는 첫 번째 사람
    values = {
        "PROFILE": user.file("profile.md").read_text().strip(),
        "RULES": user.file("rules.md").read_text().strip() if user.file("rules.md").is_file() else "(없음)",
        "TITLE": tr["title"], "CHANNEL": tr["author"], "DURATION": tr["duration_human"],
        "LANGUAGE": tr.get("language", ""), "GENERATED": "자동 생성" if tr.get("is_generated") else "수동",
        "FORMAT": "Shorts (짧은 형식)" if tr["short"] else "일반 동영상",
        "CATEGORIES": cats, "SCHEMA": _schema(tr),
        "CHAPTERS": "\n".join(tr["chapters"]) or "(자막 없음)",
        "DESCRIPTION": (tr.get("description") or "(설명란 없음)").strip(),
        "LINKS": "\n".join(f"{i}. {u}" for i, u in enumerate(description_links(tr.get("description", "")), 1))
                 or "(링크 없음)",
    }
    for k, v in values.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def _claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def call_claude(prompt: str) -> str:
    s = config.settings()["summary"]
    with tempfile.TemporaryDirectory() as d:
        try:
            p = subprocess.run(
                [_claude_bin(), "-p", "--model", s["model"], "--tools", "", "--safe-mode",
                 "--no-session-persistence", "--output-format", "json"],
                input=prompt, capture_output=True, text=True, cwd=d, timeout=s["timeout_seconds"])
        except subprocess.TimeoutExpired as e:
            raise SummaryError("transient", "claude -p 시간 초과") from e
    try:
        env = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise SummaryError("transient", f"claude -p 출력 해석 실패 (exit {p.returncode}): {(p.stderr or p.stdout)[-300:]}")
    if env.get("is_error"):
        raise SummaryError("transient", f"claude -p 오류: {str(env.get('result'))[:300]}")
    return env.get("result") or ""


def extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise SummaryError("format", "응답에 JSON 이 없음")
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError as e:
        raise SummaryError("format", f"JSON 해석 실패: {e}") from e


def _str(d: dict, k: str, required: bool = True) -> str | None:
    v = d.get(k)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise SummaryError("format", f"{k}: 비어 있지 않은 문자열이어야 함")
    return v.strip()


def _strlist(d: dict, k: str, lo: int, hi: int) -> list[str]:
    v = d.get(k)
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise SummaryError("format", f"{k}: 문자열 배열이어야 함")
    if not lo <= len(v) <= hi:
        raise SummaryError("format", f"{k}: {lo}~{hi}개여야 함 (현재 {len(v)})")
    return [x.strip() for x in v]


def _time(v, k: str) -> str:
    if not isinstance(v, str) or not TIME_RE.match(v.strip()):
        raise SummaryError("format", f"{k}: 시각 형식이 아님 ({v!r})")
    return v.strip()


def validate_links(d: dict, urls: list[str]) -> list[dict]:
    """모델이 고른 번호를 설명란 URL 로 바꾼다. 목록에 없는 번호와 형식이 틀린 항목은 버린다 (링크는 부가 정보다)."""
    out, seen = [], set()
    for x in d.get("links") or []:
        if not isinstance(x, dict) or not isinstance(x.get("label"), str) or not str(x.get("ref", "")).isdigit():
            continue
        ref, label = int(x["ref"]), x["label"].strip()
        if 1 <= ref <= len(urls) and label and ref not in seen:
            seen.add(ref)
            out.append({"url": urls[ref - 1], "label": label[:40]})
    return out[:MAX_LINKS]


def _category(d: dict) -> dict:
    cat = _str(d, "category")
    if not config.category_by_key(cat):
        raise SummaryError("format", f"category: 목록에 없는 값 ({cat})")
    prop = d.get("categoryProposal")
    return {"category": cat,
            "categoryProposal": prop.strip() if isinstance(prop, str) and prop.strip() and cat == "0099_기타" else None}


def validate_music(d: dict) -> dict:
    out = {"kind": "music", **_category(d)}
    out["artist"] = _str(d, "artist")
    out["song"] = _str(d, "song")
    songs = d.get("songs") or []
    out["songs"] = [x.strip() for x in songs if isinstance(x, str) and x.strip()][:30]
    out["album"] = _str(d, "album", required=False)
    year = d.get("releaseYear")
    year = str(year).strip() if year is not None else ""
    out["releaseYear"] = year if re.fullmatch(r"(19|20)\d\d", year) else None
    out["format"] = d.get("format") if d.get("format") in MUSIC_FORMATS else "기타"
    credits = d.get("credits") or []
    out["credits"] = [x.strip()[:200] for x in credits if isinstance(x, str) and x.strip()][:15]
    out["shortSummary"] = _str(d, "shortSummary")
    out["tags"] = _strlist(d, "tags", 1, 6)
    out["links"] = []
    return out


def validate(d: dict, short: bool, urls: list[str] | None = None, music: bool = False) -> dict:
    """형식을 검증하고 정규화한 dict 를 반환한다. 어긋나면 SummaryError(format).
    music: 음악 후보(YouTube 분류 Music)이면 음악 형식(kind music)도 받는다."""
    if music and d.get("kind") == "music":
        return validate_music(d)
    out = _category(d)
    out["summary"] = _str(d, "summary")
    out["shortSummary"] = _str(d, "shortSummary")
    rec = _str(d, "recommendation")
    if rec not in RECOMMENDATIONS:
        raise SummaryError("format", f"recommendation: 허용되지 않은 값 ({rec})")
    out["recommendation"], out["rating"] = rec, RECOMMENDATIONS[rec]
    if d.get("lifespan") not in LIFESPANS:
        raise SummaryError("format", f"lifespan: 허용되지 않은 값 ({d.get('lifespan')})")
    out["lifespan"] = d["lifespan"]
    if d.get("quality") not in QUALITIES:
        raise SummaryError("format", f"quality: 허용되지 않은 값 ({d.get('quality')})")
    out["quality"] = d["quality"]
    out["watchReason"] = _str(d, "watchReason")
    out["links"] = validate_links(d, urls or [])
    if short:
        out["keyPoint"] = _str(d, "keyPoint")
        out["tags"] = _strlist(d, "tags", 1, 5)
        return out
    out["tags"] = _strlist(d, "tags", 2, 6)
    out["keyPoints"] = _strlist(d, "keyPoints", 2, 8)
    ins = d.get("personalInsight")
    out["personalInsight"] = ins.strip() if isinstance(ins, str) and ins.strip() else None
    acts = d.get("actionableItems") or []
    out["actionableItems"] = [a.strip() for a in acts if isinstance(a, str) and a.strip()]
    claims = d.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 8:
        raise SummaryError("format", "claims: 1~8개 배열이어야 함")
    out["claims"] = []
    for i, c in enumerate(claims):
        if not isinstance(c, dict) or c.get("evidence") not in EVIDENCE:
            raise SummaryError("format", f"claims[{i}].evidence: 허용되지 않은 값")
        out["claims"].append({"text": _str(c, "text"), "evidence": c["evidence"],
                              "time": _time(c.get("time"), f"claims[{i}].time"), "flag": bool(c.get("flag"))})
    ts = d.get("timestamps")
    if not isinstance(ts, list) or not 2 <= len(ts) <= 12:
        raise SummaryError("format", "timestamps: 2~12개 배열이어야 함")
    out["timestamps"] = [{"time": _time(t.get("time"), f"timestamps[{i}].time"), "topic": _str(t, "topic")}
                         for i, t in enumerate(ts) if isinstance(t, dict)]
    return out


def to_seconds(t: str) -> int:
    parts = [int(x) for x in t.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def time_link(video_id: str, t: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={to_seconds(t)}s"


def render_music(tr: dict, s: dict) -> list[str]:
    rows = [("가수", s["artist"]), ("곡", s["song"]), ("앨범", s["album"]), ("발매", s["releaseYear"]), ("형식", s["format"])]
    lines = ["## 곡 정보", ""] + [f"- **{k}** {v}" for k, v in rows if v] + [""]
    if s["songs"]:
        lines += ["## 곡 목록", ""] + [f"{i}. {x}" for i, x in enumerate(s["songs"], 1)] + [""]
    if s["credits"]:
        lines += ["## 크레딧", ""] + [f"- {x}" for x in s["credits"]] + [""]
    return lines


def render(tr: dict, s: dict, summarized: str) -> str:
    meta = {
        "video_id": tr["video_id"], "url": tr["url"], "title": tr["title"], "channel": tr["author"],
        "duration": tr["duration_human"], "duration_seconds": tr["duration"], "published": tr.get("published", ""),
        "summarized": summarized, "short": tr["short"], "language": tr.get("language", ""),
        "is_generated": tr.get("is_generated", False), **s,
    }
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()] + ["---", ""]
    vid = tr["video_id"]
    lines += [f"# {tr['title']}", "", f"{tr['author']} · {tr['duration_human']} · [원본]({tr['url']})", ""]
    if s.get("kind") == "music":
        lines += render_music(tr, s)
        if s.get("categoryProposal"):
            lines += ["## 카테고리 추가 제안", "", s["categoryProposal"], ""]
        lines += [f"태그: {', '.join(s['tags'])}", "", "---", "",
                  "곡 정보는 YouTube 음악 정보 · 제목 · 설명란을 바탕으로 Claude가 정리했다.", ""]
        return "\n".join(lines)
    lines += [f"**평가** {s['rating']}/5 {s['recommendation']} · **수명** {s['lifespan']} · **품질** {s['quality']}", ""]
    lines += ["## 요약", "", s["summary"], ""]
    if tr["short"]:
        lines += ["## 핵심", "", s["keyPoint"], ""]
    else:
        lines += ["## 핵심 포인트", ""] + [f"{i}. {p}" for i, p in enumerate(s["keyPoints"], 1)] + [""]
        lines += ["## 주장과 근거", ""]
        for c in s["claims"]:
            flag = " — 확인 필요" if c["flag"] else ""
            lines.append(f"- [{c['evidence']}] {c['text']} ([{c['time']}]({time_link(vid, c['time'])})){flag}")
        lines.append("")
        if s["personalInsight"]:
            lines += ["## 개인 인사이트", "", s["personalInsight"], ""]
        if s["actionableItems"]:
            lines += ["## 바로 적용할 것", ""] + [f"- {a}" for a in s["actionableItems"]] + [""]
        lines += ["## 타임스탬프", ""] + [f"- [{t['time']}]({time_link(vid, t['time'])}) {t['topic']}"
                                          for t in s["timestamps"]] + [""]
    if s.get("links"):
        lines += ["## 관련 링크", ""] + [f"- [{x['label']}]({x['url']})" for x in s["links"]] + [""]
    lines += ["## 시청 권유 근거", "", s["watchReason"], ""]
    if s.get("categoryProposal"):
        lines += ["## 카테고리 추가 제안", "", s["categoryProposal"], ""]
    lines += [f"태그: {', '.join(s['tags'])}", "", "---", "",
              "요약은 자막을 바탕으로 Claude가 생성했다. 원문과 다를 수 있다.", ""]
    return "\n".join(lines)


def save_transcript(tr: dict) -> None:
    d = config.path("transcripts")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tr['video_id']}.json").write_text(json.dumps(tr, ensure_ascii=False, indent=1))


def summarize(tr: dict, attempts: int = 2) -> tuple[Path, dict]:
    """자막 dict(transcript.fetch 결과)를 요약하여 파일로 저장하고 (경로, 요약) 을 반환한다.
    형식 오류는 같은 호출 안에서 한 번 더 시도한다."""
    save_transcript(tr)
    prompt = build_prompt(tr)
    last = None
    for _ in range(attempts):
        try:
            s = validate(extract_json(call_claude(prompt)), tr["short"], description_links(tr.get("description", "")),
                         music=music_candidate(tr))
            s["links"] = resolve_links(s["links"])
            break
        except SummaryError as e:
            last = e
            if e.reason != "format":
                raise
    else:
        raise last
    summarized = datetime.now(KST).strftime("%Y-%m-%d")
    out = config.path("summaries") / s["category"] / f"{summarized}_{tr['video_id']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    # 같은 동영상의 이전 요약이 있으면 교체한다
    for old in config.path("summaries").glob(f"*/*_{tr['video_id']}.md"):
        if old != out:
            old.unlink()
    tmp = out.with_suffix(".tmp")
    tmp.write_text(render(tr, s, summarized))
    tmp.replace(out)
    return out, s


def read_front_matter(path: Path) -> dict:
    meta = {}
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        return meta
    for line in lines[1:]:
        if line == "---":
            break
        k, _, v = line.partition(": ")
        meta[k] = json.loads(v)
    return meta
