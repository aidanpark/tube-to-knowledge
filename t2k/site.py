"""요약 파일(summaries/)로 정적 사이트(site/)를 생성한다. 빌드 단계 없이 HTML · CSS · JS 만 만든다.

화면: 홈(index) · 동영상 요약(v/<id>) · Shorts 요약(v/<id>) · 카테고리 목록 · 카테고리 상세(c/<id>) ·
주간 요약 목록 · 주간 요약(w/<YYYY-Www>/<카테고리 id>, weekly.py 가 만든 파일) · 검색(search.json 을 브라우저에서 검색).

요약 본문은 모델이 생성한 글이므로 모든 값을 이스케이프한다 (Jinja2 autoescape).
화면 문구는 [general] language 에 따른다 (i18n.py, locales/<언어>/site.toml). 저장된 값(시청 권유 · 수명 등)은 그대로 두고 표시할 때만 바꾼다.
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

from . import config, i18n, weekly
from .summarize import now as local_now, read_front_matter, time_link

HERE = Path(__file__).parent
TEMPLATES = HERE / "site_templates"
ASSETS = HERE / "site_assets"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# 시안의 강조 바탕 순서 (카테고리 목록 화면). 0099 기타는 회색
CAT_COLORS = [("c-mint", "#0D652D"), ("c-blue", "#174EA6"), ("c-sky", "#0B4A6F"), ("c-lime", "#3C4A0B")]
LIFE_CLASS = {"짧음": "c-amber", "중간": "c-gray", "긺": "c-sky"}
LIFE_RANK = {"짧음": 1, "중간": 2, "긺": 3}
EVIDENCE_CLASS = {"연구 인용": "c-mint", "데이터": "c-mint", "시연": "c-blue", "의견": "c-gray", "일화": "c-gray"}
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


def tn(key: str, n: int, **kw) -> str:
    """개수가 들어간 문구. 1이면 <키>.one 이 있을 때 그것을 쓴다 (영어의 단수)."""
    one = f"{key}.one"
    return i18n.t(one if n == 1 and one in i18n.catalog(i18n.lang()) else key, n=n, **kw)


def label(kind: str, value) -> str:
    """저장된 값의 표시 이름 (locales 의 [label.<kind>]). 없으면 값을 그대로 쓴다."""
    return i18n.table(f"label.{kind}").get(str(value), value or "")


def week_of(d: date) -> dict:
    y, w, _ = d.isocalendar()
    mon = d - timedelta(days=d.weekday())
    sun = mon + timedelta(days=6)
    return {"key": f"{y}-W{w:02d}", "label": i18n.t("week.label", n=w), "range": f"{mon:%m/%d}–{sun:%m/%d}"}


def load_summaries(src: Path) -> list[dict]:
    """요약 파일을 읽어 화면에 필요한 값을 덧붙인다. 최신순으로 반환한다."""
    out = []
    for p in sorted(src.glob("*/*.md")):
        m = read_front_matter(p)
        vid = m.get("video_id", "")
        if not ID_RE.match(vid):
            continue
        cat = config.category_by_key(m.get("category", "")) or config.other_category()
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
        v["quality_ko"] = label("quality", m.get("quality", ""))  # 이름은 사람별 템플릿과의 호환으로 남긴다. 값은 언어를 따른다
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
        v["weekly"] = None  # build 에서 주간 요약과 연결한다
        out.append(v)
    # 같은 동영상을 다시 요약한 경우 summarize 가 이전 파일을 지우지만, 남아 있으면 최신 것만 쓴다
    out.sort(key=lambda v: v["sort_key"], reverse=True)
    seen, uniq = set(), []
    for v in out:
        if v["video_id"] not in seen:
            seen.add(v["video_id"])
            uniq.append(v)
    return uniq


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
                                    "reason_ko": label("failed", r.get("reason", ""))}  # 이름은 호환으로 남긴다 (pipeline · 디스코드 알림)
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
        out.append(i18n.t("notice.blocked", hours=hours))
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
            "weeklies": [],  # build 에서 채운다
        })
    return views


def weekly_views(docs: list[dict], items: list[dict]) -> list[dict]:
    """주간 요약 파일(weekly.load_all)에 화면 값을 덧붙인다. 영상은 지금 있는 요약과 연결한다 (지웠으면 제목만)."""
    by_id = {v["video_id"]: v for v in items}
    cats = {c.id: c for c in config.categories()}
    days = i18n.t("week.weekdays").split(",")
    out = []
    for d in docs:
        mon, sun = weekly.week_dates(d["week"])
        wk = week_of(mon)
        cat = cats.get(d["cat_id"]) or config.Category(d["cat_id"], d["cat_name"], "")
        bg, _ = cat_style(cat) if d["cat_id"] in cats else ("c-gray", "#3C4043")
        videos = []
        for x in d["videos"]:
            v = by_id.get(x["id"])
            videos.append({**x, "v": v, "href": v["href"] if v else None, "title": v.get("title", x["title"]) if v else x["title"]})
        vmap = {x["id"]: x for x in videos}
        out.append({
            **d, "cat": cat, "bg_class": bg, "href": f"w/{d['week']}/{d['cat_id']}.html", "count": len(videos),
            "label": wk["label"], "year_label": _year_label(d["week"]), "range": wk["range"], "range_label": wk["range"],
            "range_long": f"{mon:%m/%d}({days[mon.weekday()]}) – {sun:%m/%d}({days[sun.weekday()]})",
            "previous_label": _year_label(d["previous"]) if d.get("previous") else None,
            "video_views": videos,
            "merged_views": [{**g, "refs": [vmap[i] for i in g["videos"] if i in vmap]} for g in d["merged"]],
            "shorts_views": [{**g, "refs": [vmap[i] for i in g["videos"] if i in vmap]} for g in d["shorts"]],
            "pick_views": [{**p, "ref": vmap[p["id"]]} for p in d["picks"] if p["id"] in vmap],
        })
    return out


def _year_label(key: str) -> str:
    """'2026-W42' → 2026년 42주 · Week 42, 2026."""
    y, w = key.split("-W")
    return i18n.t("week.year_label", year=y, n=int(w))


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
          now: datetime | None = None, status_dir: Path | None = None, weekly_dir: Path | None = None) -> Path:
    """사이트를 임시 폴더에 생성한 뒤 out 과 교체한다. 생성 중 오류가 나도 기존 사이트는 그대로 남는다.
    status_dir: 파이프라인 상태(status.json, 사람 모두에게 같다)가 있는 폴더. 없으면 state.
    weekly_dir: 주간 요약 폴더. 없으면 src 옆의 weekly/."""
    src = src or config.path("summaries")
    weekly_dir = weekly_dir or src.parent / "weekly"
    out = out or config.path("site")
    state = state or config.path("state")
    now = now or local_now()

    items = load_summaries(src) if src.exists() else []
    week = week_of(now.date())
    cats = category_views(items, week["key"])
    by_cat = {c["cat"].id: c for c in cats}
    weeklies = weekly_views(weekly.load_all(weekly_dir), items)  # 최신 주가 먼저
    for w in weeklies:
        if w["cat_id"] in by_cat:
            by_cat[w["cat_id"]]["weeklies"].append(w)
        for x in w["video_views"]:
            if x["v"] and x["v"]["weekly"] is None:  # 영상이 여러 주에 들면 가장 최근 주
                x["v"]["weekly"] = {"href": w["href"], "label": f"{w['cat_name']} · {w['label']}"}
    weeks = []
    for w in weeklies:
        if not weeks or weeks[-1]["key"] != w["week"]:
            weeks.append({"key": w["week"], "label": w["year_label"], "range": w["range_label"], "docs": []})
        weeks[-1]["docs"].append(w)
    # 사람별 덧칠: users/<id>/templates 의 같은 이름 템플릿이 기본 템플릿보다 먼저 쓰인다
    user = config.current_user()
    custom = [str(user.file("templates"))] if user and user.file("templates").is_dir() else []
    # 사람별 템플릿은 디스코드 지시로 바뀌므로 샌드박스에서 렌더링한다 (템플릿에서 파이썬 내부에 접근하지 못하게)
    env = SandboxedEnvironment(loader=FileSystemLoader(custom + [str(TEMPLATES)]),
                      autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    # 화면 문구: t("키") · tn("키", 개수) · label("종류", 저장된 값). 브라우저 스크립트의 문구는 js_i18n 으로 넘긴다
    env.globals.update(t=i18n.t, tn=tn, label=label, lang=i18n.lang(), app_name=i18n.t("app.name"))
    js_i18n = {**i18n.table("js"), **{f"life.{k}": v for k, v in i18n.table("label.life").items()},
               **{f"type.{k}": v for k, v in i18n.table("label.type").items()},
               **{f"kind.{k}": v for k, v in i18n.table("label.kind").items()}}
    user_css = user.file("style.css") if user and user.file("style.css").is_file() else None
    common = {"built_at": now.strftime("%Y-%m-%d %H:%M"), "build_id": build_id(user_css),
              "all_cats": config.categories(), "other_id": config.OTHER_ID, "user_css": bool(user_css),
              "other_name": config.other_category().name, "js_i18n": js_i18n}

    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "v").mkdir(parents=True)
    (tmp / "c").mkdir()
    (tmp / "w").mkdir()
    shutil.copytree(ASSETS, tmp / "assets")
    if user_css:
        shutil.copy(user_css, tmp / "assets" / "user.css")

    def page(path: str, template: str, **ctx) -> None:
        root = "../" * path.count("/")
        (tmp / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp / path).write_text(env.get_template(template).render(**common, root=root, **ctx))

    week_items = [v for v in items if v["week"] == week["key"]]
    done = {v["video_id"] for v in items}  # 나중에 (수동으로) 요약된 동영상은 실패 목록에서 뺀다
    failed = [f for f in load_failed(state) if f["video_id"] not in done]
    notices = status_notices(status_dir or state, now)
    week_docs = [w for w in weeklies if w["week"] == weekly.last_week(now)]  # 이번 주에 새로 나온 주간 요약
    page("index.html", "home.html", title=i18n.t("app.name"), tab="recent", week=week, week_docs=week_docs,
         week_items=week_items, week_cats=[c["cat"].name for c in cats if c["week_count"]],
         recent=items[:20], top_cats=sorted([c for c in cats if c["count"]], key=lambda c: -c["count"])[:6],
         failed_count=len(failed), notices=notices)
    page("categories.html", "categories.html", title=i18n.t("cats.title"), tab="categories", cats=cats, total=len(items))
    for c in cats:
        page(f"c/{c['cat'].id}.html", "category.html", title=c["cat"].name, tab="categories", c=c)
    for v in items:
        sib = by_cat[v["cat"].id]["videos"]
        i = sib.index(v)
        tpl = "music.html" if v.get("kind") == "music" else "short.html" if v["short"] else "summary.html"
        page(v["href"], tpl, title=v.get("title", ""), tab="",
             v=v, c=by_cat[v["cat"].id], prev=sib[i + 1] if i + 1 < len(sib) else None, next=sib[i - 1] if i else None)
    page("weeks.html", "weeks.html", title=i18n.t("weeks.title"), tab="weeks", weeks=weeks)
    for w in weeklies:
        same = [x for x in weeklies if x["cat_id"] == w["cat_id"]]  # 최신이 먼저
        i = same.index(w)
        page(w["href"], "weekly.html", title=i18n.t("weekly.page_title", cat=w["cat_name"], label=w["label"]), tab="weeks", w=w,
             c=by_cat.get(w["cat_id"]), prev=same[i + 1] if i + 1 < len(same) else None, next=same[i - 1] if i else None)
    page("search.html", "search.html", title=i18n.t("search.title"), tab="search")
    page("failed.html", "failed.html", title=i18n.t("failed.title"), tab="", failed=failed)
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
