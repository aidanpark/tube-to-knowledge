"""요약 파일(summaries/)로 정적 사이트(site/)를 생성한다. 빌드 단계 없이 HTML · CSS · JS 만 만든다.

화면: 홈(index) · 동영상 요약(v/<id>) · Shorts 요약(v/<id>) · 카테고리 목록 · 카테고리 상세(c/<id>) ·
주간 요약 목록 · 검색(search.json 을 브라우저에서 검색).

요약 본문은 모델이 생성한 글이므로 모든 값을 이스케이프한다 (Jinja2 autoescape).
"""
import hashlib
import json
import re
import shutil
from urllib.parse import urlencode, urlsplit
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import FileSystemLoader, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

from . import config
from .summarize import KST, read_front_matter, time_link

HERE = Path(__file__).parent
TEMPLATES = HERE / "site_templates"
ASSETS = HERE / "site_assets"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# 시안의 강조 바탕 순서 (카테고리 목록 화면). 0099 기타는 회색
CAT_COLORS = [("c-mint", "#0D652D"), ("c-blue", "#174EA6"), ("c-sky", "#0B4A6F"), ("c-lime", "#3C4A0B")]
LIFE_CLASS = {"짧음": "c-amber", "중간": "c-gray", "긺": "c-sky"}
LIFE_RANK = {"짧음": 1, "중간": 2, "긺": 3}
EVIDENCE_CLASS = {"연구 인용": "c-mint", "데이터": "c-mint", "시연": "c-blue", "의견": "c-gray", "일화": "c-gray"}
QUALITY_KO = {"high": "높음", "medium": "보통", "low": "낮음"}
PROPOSAL_RE = re.compile(r"^\s*\d{4}_([^:：]{1,20})\s*[:：]\s*(.*)$", re.S)  # summarize 의 「NNNN_이름: 근거」


def proposal_href(text: str | None) -> str | None:
    """카테고리 추가 제안을 카테고리 화면의 새 카테고리 입력란으로 넘기는 주소. 형식이 다르면 None."""
    m = PROPOSAL_RE.match(text or "")
    if not m:
        return None
    return "categories.html?" + urlencode({"name": m.group(1).strip(), "scope": m.group(2).strip()[:150]}) + "#new"


def cat_style(cat: config.Category) -> tuple[str, str]:
    if cat.id == "0099":
        return "c-gray", "#3C4043"
    idx = [c.id for c in config.categories()].index(cat.id)
    return CAT_COLORS[idx % len(CAT_COLORS)]


def week_of(d: date) -> dict:
    y, w, _ = d.isocalendar()
    mon = d - timedelta(days=d.weekday())
    sun = mon + timedelta(days=6)
    return {"key": f"{y}-W{w:02d}", "label": f"{w}주", "range": f"{mon:%m/%d}–{sun:%m/%d}"}


def load_summaries(src: Path) -> list[dict]:
    """요약 파일을 읽어 화면에 필요한 값을 덧붙인다. 최신순으로 반환한다."""
    out = []
    for p in sorted(src.glob("*/*.md")):
        m = read_front_matter(p)
        vid = m.get("video_id", "")
        if not ID_RE.match(vid):
            continue
        cat = config.category_by_key(m.get("category", "")) or config.category_by_key("0099_기타")
        bg, _ = cat_style(cat)
        v = {**m, "cat": cat, "cat_class": bg, "href": f"v/{vid}.html"}
        v["short"] = bool(m.get("short"))
        v["url"] = f"https://www.youtube.com/shorts/{vid}" if v["short"] else f"https://www.youtube.com/watch?v={vid}"
        v["url_label"] = v["url"].removeprefix("https://www.")
        v["thumb"] = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
        # hqdefault(4:3)는 위아래 검은 띠가 있어 16:9 로 잘라 쓴다. Shorts 의 세로 상자에는 띠가 없는 mqdefault 를 쓴다.
        # hq720 은 없는 동영상이 있고, 없으면 404 와 함께 회색 그림을 반환하므로 쓰지 않는다
        v["thumb_large"] = v["thumb"] if v["short"] else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        v["life_class"] = LIFE_CLASS.get(m.get("lifespan"), "c-gray")
        v["life_rank"] = LIFE_RANK.get(m.get("lifespan"), 0)
        v["quality_ko"] = QUALITY_KO.get(m.get("quality"), m.get("quality", ""))
        # 같은 날 요약한 것끼리는 파일 수정 시각으로 순서를 정한다
        v["sort_key"] = f"{m.get('summarized', '')} {int(p.stat().st_mtime):010d}"
        v["week"] = week_of(date.fromisoformat(m["summarized"]))["key"] if m.get("summarized") else ""
        v["claims"] = [{**c, "ev_class": EVIDENCE_CLASS.get(c["evidence"], "c-gray"), "href": time_link(vid, c["time"])}
                       for c in m.get("claims") or []]
        v["timestamps"] = [{**t, "href": time_link(vid, t["time"])} for t in m.get("timestamps") or []]
        v["ts_hms"] = any(t["time"].count(":") == 2 for t in v["timestamps"])  # 시각 열 폭을 H:MM:SS 에 맞춘다
        # 관련 링크: http(s) 만 허용하고 화면에는 주소의 호스트를 함께 표시한다
        v["links"] = [{**x, "host": urlsplit(x["url"]).netloc.removeprefix("www.")}
                      for x in m.get("links") or [] if str(x.get("url", "")).startswith(("https://", "http://"))]
        v["proposal_href"] = proposal_href(m.get("categoryProposal"))
        v["weekly"] = None  # 5단계에서 주간 요약과 연결한다
        out.append(v)
    # 같은 동영상을 다시 요약한 경우 summarize 가 이전 파일을 지우지만, 남아 있으면 최신 것만 쓴다
    out.sort(key=lambda v: v["sort_key"], reverse=True)
    seen, uniq = set(), []
    for v in out:
        if v["video_id"] not in seen:
            seen.add(v["video_id"])
            uniq.append(v)
    return uniq


FAILED_REASON_KO = {"no_transcript": "자막 없음", "unavailable": "비공개 · 삭제", "format": "요약 형식 오류",
                    "transient": "일시 오류", "blocked": "YouTube 차단"}


def load_failed(state: Path) -> list[dict]:
    """state/failed.jsonl 의 실패 기록. 같은 동영상은 마지막 기록만 남기고 최신순으로 반환한다."""
    f = state / "failed.jsonl"
    if not f.exists():
        return []
    by_id = {}
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if ID_RE.match(r.get("video_id", "")):
            by_id[r["video_id"]] = {**r, "time": r.get("time", ""),
                                    "reason_ko": FAILED_REASON_KO.get(r.get("reason"), r.get("reason", ""))}
    return sorted(by_id.values(), key=lambda r: r["time"], reverse=True)


def status_notices(state: Path, now: datetime) -> list[str]:
    """파이프라인 상태(state/status.json)에서 사이트에 표시할 안내를 만든다."""
    f = state / "status.json"
    if not f.exists():
        return []
    d = json.loads(f.read_text())
    out = []
    since = d.get("blocked_since")
    if since and now.timestamp() - since >= 4 * 3600:
        hours = int((now.timestamp() - since) // 3600)
        out.append(f"YouTube 가 자막 요청을 {hours}시간째 막고 있습니다. 새 동영상 요약이 멈춰 있습니다.")
    return out


def category_views(items: list[dict], this_week: str) -> list[dict]:
    views = []
    for cat in config.categories():
        mine = [v for v in items if v["cat"].id == cat.id]
        bg, fg = cat_style(cat)
        views.append({
            "cat": cat, "bg_class": bg, "fg": fg, "href": f"c/{cat.id}.html", "videos": mine, "count": len(mine),
            "week_count": sum(v["week"] == this_week for v in mine),
            "top_count": sum(v.get("rating", 0) >= 4 for v in mine),
            "weeklies": [],  # 5단계
        })
    return views


def search_index(items: list[dict]) -> dict:
    rows = []
    for v in items:
        body = [v.get("shortSummary", ""), v.get("summary", ""), v.get("artist", ""), v.get("song", ""), v.get("album") or ""]
        body += v.get("songs") or []
        body += v.get("keyPoints") or ([v["keyPoint"]] if v.get("keyPoint") else [])
        rows.append({
            "type": "요약", "id": v["video_id"], "title": v.get("title", ""), "channel": v.get("channel", ""),
            "category": v["cat"].name, "rating": v.get("rating", 0), "short": v["short"], "life": v.get("lifespan", ""),
            "date": v.get("summarized", "")[5:], "sort": v["sort_key"], "tags": v.get("tags") or [],
            "text": " ".join(b for b in body if b), "href": v["href"],
        })
    return {"items": rows}


def build_id(user_css: Path | None = None) -> str:
    h = hashlib.sha256()
    for p in sorted(ASSETS.iterdir()) + ([user_css] if user_css else []):
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


def build(src: Path | None = None, out: Path | None = None, state: Path | None = None,
          now: datetime | None = None, status_dir: Path | None = None) -> Path:
    """사이트를 임시 폴더에 생성한 뒤 out 과 교체한다. 생성 중 오류가 나도 기존 사이트는 그대로 남는다.
    status_dir: 파이프라인 상태(status.json, 사람 모두에게 같다)가 있는 폴더. 없으면 state."""
    src = src or config.path("summaries")
    out = out or config.path("site")
    state = state or config.path("state")
    now = now or datetime.now(KST)

    items = load_summaries(src) if src.exists() else []
    week = week_of(now.date())
    cats = category_views(items, week["key"])
    by_cat = {c["cat"].id: c for c in cats}
    # 사람별 덧칠: users/<id>/templates 의 같은 이름 템플릿이 기본 템플릿보다 먼저 쓰인다
    user = config.current_user()
    custom = [str(user.file("templates"))] if user and user.file("templates").is_dir() else []
    # 사람별 템플릿은 디스코드 지시로 바뀌므로 샌드박스에서 렌더링한다 (템플릿에서 파이썬 내부에 접근하지 못하게)
    env = SandboxedEnvironment(loader=FileSystemLoader(custom + [str(TEMPLATES)]),
                      autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    user_css = user.file("style.css") if user and user.file("style.css").is_file() else None
    common = {"built_at": now.strftime("%Y-%m-%d %H:%M"), "build_id": build_id(user_css),
              "all_cats": config.categories(), "other_id": config.OTHER_ID, "user_css": bool(user_css)}

    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "v").mkdir(parents=True)
    (tmp / "c").mkdir()
    shutil.copytree(ASSETS, tmp / "assets")
    if user_css:
        shutil.copy(user_css, tmp / "assets" / "user.css")

    def page(path: str, template: str, **ctx) -> None:
        root = "../" * path.count("/")
        (tmp / path).write_text(env.get_template(template).render(**common, root=root, **ctx))

    week_items = [v for v in items if v["week"] == week["key"]]
    done = {v["video_id"] for v in items}  # 나중에 (수동으로) 요약된 동영상은 실패 목록에서 뺀다
    failed = [f for f in load_failed(state) if f["video_id"] not in done]
    notices = status_notices(status_dir or state, now)
    page("index.html", "home.html", title="영상 요약", tab="recent", week=week, week_docs=[],
         week_items=week_items, week_cats=[c["cat"].name for c in cats if c["week_count"]],
         recent=items[:20], top_cats=sorted([c for c in cats if c["count"]], key=lambda c: -c["count"])[:6],
         failed_count=len(failed), notices=notices)
    page("categories.html", "categories.html", title="카테고리", tab="categories", cats=cats, total=len(items))
    for c in cats:
        page(f"c/{c['cat'].id}.html", "category.html", title=c["cat"].name, tab="categories", c=c)
    for v in items:
        sib = by_cat[v["cat"].id]["videos"]
        i = sib.index(v)
        tpl = "music.html" if v.get("kind") == "music" else "short.html" if v["short"] else "summary.html"
        page(v["href"], tpl, title=v.get("title", ""), tab="",
             v=v, c=by_cat[v["cat"].id], prev=sib[i + 1] if i + 1 < len(sib) else None, next=sib[i - 1] if i else None)
    page("weeks.html", "weeks.html", title="주간 요약", tab="weeks", weeks=[])
    page("search.html", "search.html", title="검색", tab="search")
    page("failed.html", "failed.html", title="요약 실패", tab="", failed=failed)
    # Access 로 보호하지만 검색 엔진 수집도 막는다: robots.txt, 응답 헤더, 페이지의 meta 세 겹
    (tmp / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (tmp / "_headers").write_text("/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n")
    (tmp / "search.json").write_text(json.dumps(search_index(items), ensure_ascii=False, separators=(",", ":")))

    if out.exists():
        old = out.with_name(out.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        out.rename(old)
        tmp.rename(out)
        shutil.rmtree(old)
    else:
        tmp.rename(out)
    return out
