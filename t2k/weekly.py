"""주간 요약: 한 주(월~일) 동안 요약한 영상을 카테고리별로 묶어 Claude 로 정리한다.

일정: 매주 일요일 24:00(월요일 0시, 이 맥의 현지 시간)이 지나면 파이프라인이 지난주의 주간 요약을 만든다 (pipeline 의 주간 요약 단계).
한 주기에 한 건씩 만들고, 영상 요약 대기열이 비어 있을 때만 실행한다. 맥이 꺼져 있었으면 켜진 뒤의 주기에서 만든다.
지난주만 만든다: 그보다 오래된 주는 `t2k weekly <YYYY-Www>` 로 직접 만든다.

대상: 그 주에 요약한(요약일 기준) 영상이 [weekly] min_items 편 이상인 카테고리. 음악 형식은 세지 않는다.
파일: users/<id>/weekly/<YYYY-Www>/<카테고리 id>.json (커밋한다). 사이트 생성(site.py)이 이 파일을 읽는다.

claude -p 는 영상 요약과 같은 방식(빈 임시 디렉터리, 도구 없음, --safe-mode)으로 호출한다.
요약 글은 신뢰하지 않는 자막에서 생성한 것이므로 프롬프트에서 데이터로만 다룬다.

언어 ([general] language): 프롬프트는 prompts/<언어>/weekly.txt 이다. 변경 종류는 모델이 언어별 표기로 쓰고(CHANGE_TOKENS),
저장은 한국어 코드(새로움 · 대체됨)로 한다. 프롬프트에 넣는 영상의 시청 권유 · 수명도 그 언어의 표기로 바꾼다.
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, i18n
from .i18n import t
from .summarize import now as local_now, TOKENS, SummaryError, call_claude, extract_json, prompt_file

CHANGE_KINDS = ("새로움", "대체됨")
CHANGE_TOKENS = {"en": {"new": "새로움", "replaced": "대체됨"}}  # 모델이 쓰는 표기 → 저장 코드
MAX_TEXT = 400


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_dates(key: str) -> tuple[date, date]:
    """'2026-W42' → (월요일, 일요일)."""
    y, w = key.split("-W")
    mon = date.fromisocalendar(int(y), int(w), 1)
    return mon, mon + timedelta(days=6)


def last_week(now: datetime) -> str:
    """now(현지 시각) 기준으로 끝난 가장 최근 주. 월요일 0시가 지나면 바로 앞 주가 된다.
    now 의 날짜를 그대로 쓴다: 시간대 변환은 호출하는 쪽이 한다 (파이프라인은 현지 시각을 넘긴다)."""
    return week_key(now.date() - timedelta(days=7))


def doc_path(key: str, cat_id: str) -> Path:
    return config.path("weekly") / key / f"{cat_id}.json"


def _attempts_file() -> Path:
    return config.path("state") / "weekly.json"


def _attempts() -> dict:
    f = _attempts_file()
    return json.loads(f.read_text()) if f.exists() else {}


def _set_attempts(name: str, n: int) -> None:
    d = _attempts()
    d[name] = n
    f = _attempts_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1))


def candidates(key: str, items: list[dict] | None = None) -> list[tuple[config.Category, list[dict]]]:
    """그 주에 정리할 카테고리와 영상 (지금 사람). 이미 만든 것과 시도 횟수를 넘긴 것은 뺀다."""
    if items is None:
        from . import site  # site 가 이 모듈을 가져오므로 여기서 가져온다
        src = config.path("summaries")
        items = site.load_summaries(src) if src.exists() else []
    s = config.settings()["weekly"]
    tried = _attempts()
    out = []
    for cat in config.categories():
        vids = [v for v in items if v["week"] == key and v["cat"].id == cat.id and v.get("kind") != "music"]
        if len(vids) < s["min_items"] or doc_path(key, cat.id).exists():
            continue
        if tried.get(f"{key}/{cat.id}", 0) >= s["max_attempts"]:
            continue
        out.append((cat, sorted(vids, key=lambda v: v["sort_key"])))
    return out


def previous(key: str, cat_id: str) -> dict | None:
    """같은 카테고리의 직전 주간 요약 (key 보다 앞선 주 중 가장 최근)."""
    root = config.path("weekly")
    older = sorted((p for p in root.glob(f"*/{cat_id}.json") if p.parent.name < key), key=lambda p: p.parent.name)
    return json.loads(older[-1].read_text()) if older else None


def _token(kind: str, code: str) -> str:
    """저장 코드를 그 언어에서 모델이 쓰는 표기로 바꾼다 (summarize.TOKENS 의 역방향). 한국어는 코드 그대로."""
    return next((k for k, v in TOKENS.get(i18n.lang(), {}).get(kind, {}).items() if v == code), code)


def _video_text(v: dict) -> str:
    lines = [f"[{v['video_id']}] {v.get('title', '')}",
             f"{t('weekly.channel')}: {v.get('channel', '')} · {t('weekly.format')}: {t('weekly.shorts' if v['short'] else 'weekly.video')}"
             f" · {t('weekly.recommendation')}: {_token('recommendation', v.get('recommendation', ''))}"
             f" · {t('weekly.lifespan')}: {_token('lifespan', v.get('lifespan', ''))}",
             f"{t('weekly.one_line')}: {v.get('shortSummary', '')}"]
    points = v.get("keyPoints") or ([v["keyPoint"]] if v.get("keyPoint") else [])
    lines += [f"- {p}" for p in points]
    flagged = [c["text"] for c in v.get("claims") or [] if c.get("flag")]
    if flagged:
        lines.append(f"{t('weekly.flagged')}: " + " / ".join(flagged))
    return "\n".join(lines)


def build_prompt(key: str, cat: config.Category, vids: list[dict], prev: dict | None) -> str:
    mon, sun = week_dates(key)
    user = config.current_user() or config.default_user()
    if prev:
        previous_text = (f"## {t('weekly.previous')} (" + prev["week"] + ")\n---BEGIN UNTRUSTED PREVIOUS---\n"
                         + prev["headline"] + "\n" + "\n".join(f"- {h}" for h in prev["highlights"])
                         + "\n---END UNTRUSTED PREVIOUS---")
        rule = t("weekly.changes_rule")
    else:
        previous_text = ""
        rule = t("weekly.changes_none")
    values = {
        "CATEGORY": cat.name, "SCOPE": cat.scope, "COUNT": str(len(vids)),
        "RANGE": t("weekly.range", mon=f"{mon:%Y-%m-%d}", sun=f"{sun:%Y-%m-%d}"),
        "PROFILE": user.file("profile.md").read_text().strip() if user.file("profile.md").is_file() else t("weekly.none"),
        "CHANGES_RULE": rule, "PREVIOUS": previous_text,
        "SUMMARIES": "\n\n".join(_video_text(v) for v in vids),
    }
    tpl = prompt_file("weekly.txt").read_text()
    for k, v in values.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def _text(x, limit: int = MAX_TEXT) -> str | None:
    return x.strip()[:limit] if isinstance(x, str) and x.strip() else None


def validate(d: dict, ids: list[str], short_ids: set[str], has_prev: bool) -> dict:
    """형식을 검증한다. 제목과 핵심이 어긋나면 SummaryError(format), 나머지 항목은 어긋난 것만 버린다."""
    headline = _text(d.get("headline"), 100)
    if not headline:
        raise SummaryError("format", "headline: 비어 있지 않은 문자열이어야 함")
    hl = d.get("highlights")
    if not isinstance(hl, list) or not all(_text(x) for x in hl) or not 1 <= len(hl) <= 5:
        raise SummaryError("format", "highlights: 문자열 1~5개 배열이어야 함")

    def groups(key: str, allowed: set[str], limit: int) -> list[dict]:
        out = []
        for g in d.get(key) or []:
            if not isinstance(g, dict):
                continue
            vids = [v for v in dict.fromkeys(g.get("videos") or []) if v in allowed]
            title, summary = _text(g.get("title"), 100), _text(g.get("summary"))
            if title and summary and len(vids) >= 2:
                out.append({"title": title, "summary": summary, "videos": vids})
        return out[:limit]

    tokens = CHANGE_TOKENS.get(i18n.lang(), {})
    kinds = [(tokens.get(c.get("kind"), c.get("kind")), c) for c in d.get("changes") or [] if isinstance(c, dict)]
    changes = [{"kind": kind, "text": _text(c.get("text"))} for kind, c in kinds
               if kind in CHANGE_KINDS and _text(c.get("text")) and (has_prev or kind == "새로움")]
    picks, seen = [], set()
    for p in d.get("picks") or []:
        if isinstance(p, dict) and p.get("id") in ids and p["id"] not in seen and _text(p.get("reason")):
            seen.add(p["id"])
            picks.append({"id": p["id"], "reason": _text(p["reason"])})
    return {"headline": headline, "highlights": [_text(x) for x in hl],
            "merged": groups("merged", set(ids), 3), "shorts": groups("shorts", short_ids, 2),
            "changes": changes[:4] if has_prev else [], "picks": picks[:3]}


def generate(key: str, cat: config.Category, vids: list[dict], attempts: int = 2) -> Path:
    """한 카테고리의 주간 요약을 만들어 저장하고 경로를 반환한다. 형식 오류는 같은 호출 안에서 한 번 더 시도한다.
    형식 오류가 남으면 시도 횟수를 기록하고 SummaryError(format) 를 던진다. 일시 오류(transient)는 기록하지 않는다."""
    prev = previous(key, cat.id)
    prompt = build_prompt(key, cat, vids, prev)
    ids = [v["video_id"] for v in vids]
    short_ids = {v["video_id"] for v in vids if v["short"]}
    model = config.settings()["weekly"]["model"]
    last = None
    for _ in range(attempts):
        try:
            body = validate(extract_json(call_claude(prompt, model)), ids, short_ids, prev is not None)
            break
        except SummaryError as e:
            last = e
            if e.reason != "format":
                raise
    else:
        name = f"{key}/{cat.id}"
        _set_attempts(name, _attempts().get(name, 0) + 1)
        raise last
    mon, sun = week_dates(key)
    doc = {
        "week": key, "category": cat.key, "cat_id": cat.id, "cat_name": cat.name,
        "range": [mon.isoformat(), sun.isoformat()], "generated": local_now().isoformat(timespec="seconds"),
        "previous": prev["week"] if prev else None,
        # 영상 목록을 함께 남긴다: 나중에 요약을 지우거나 옮겨도 그 주에 무엇을 정리했는지 남는다
        "videos": [{"id": v["video_id"], "title": v.get("title", ""), "short": v["short"],
                    "rating": v.get("rating", 0), "duration": v.get("duration", "")} for v in vids],
        **body,
    }
    out = doc_path(key, cat.id)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    tmp.replace(out)
    return out


def load_all(root: Path | None = None) -> list[dict]:
    """저장된 주간 요약 전부. 최신 주가 먼저, 같은 주는 카테고리 id 순서."""
    root = root or config.path("weekly")
    docs = []
    for p in root.glob("*/*.json") if root.exists() else []:
        try:
            docs.append(json.loads(p.read_text()))
        except ValueError:
            continue
    return sorted(docs, key=lambda d: (tuple(-int(x) for x in d["week"].split("-W")), d["cat_id"]))
