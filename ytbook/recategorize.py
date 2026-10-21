"""요약의 카테고리를 바꾼다 (카테고리를 추가하거나 분류를 고칠 때).

요약 파일의 앞머리 category 를 바꾸고 새 카테고리 폴더로 옮긴다. 요약 내용은 그대로 둔다.
"""
from pathlib import Path

from . import config
from .summarize import read_front_matter, render

TR_KEYS = ("video_id", "url", "title", "short", "published", "language", "is_generated")
META_KEYS = TR_KEYS + ("channel", "duration", "duration_seconds", "summarized")


def rewrite(path: Path, meta: dict, links: list[dict]) -> None:
    """앞머리(meta)로 요약 파일을 다시 쓴다. 본문은 앞머리에서 다시 생성한다."""
    tr = {k: meta.get(k) for k in TR_KEYS}
    tr.update(author=meta["channel"], duration_human=meta["duration"], duration=meta["duration_seconds"])
    s = {k: v for k, v in meta.items() if k not in META_KEYS}
    s["links"] = links
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render(tr, s, meta["summarized"]))
    tmp.replace(path)


def move(video_ids: list[str], key: str, summaries: Path | None = None) -> list[str]:
    if not config.category_by_key(key):
        raise ValueError(f"카테고리 목록에 없는 값: {key}")
    summaries = summaries or config.path("summaries")
    moved = []
    for vid in video_ids:
        for path in list(summaries.glob(f"*/*_{vid}.md")):  # 옮긴 파일을 다시 찾지 않도록 먼저 목록을 만든다
            meta = read_front_matter(path)
            meta["category"] = key
            meta["categoryProposal"] = None  # 제안이 받아들여졌거나 다른 분류로 정리되었다
            dest = summaries / key / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            rewrite(dest, meta, meta.get("links") or [])
            if dest != path:
                path.unlink()
            moved.append(f"{vid} {meta.get('title', '')}")
    return moved
