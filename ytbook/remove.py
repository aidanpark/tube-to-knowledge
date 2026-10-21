"""요약을 삭제한다 (수명이 짧은 요약 등).

요약 파일과 자막 기록을 지우고 state/removed.jsonl 에 남긴다. backlog 는 이 목록의 동영상을 다시 요약하지 않는다.
사이트에 반영하려면 사이트를 다시 생성하고 커밋 · 푸시한다.
"""
import json
from datetime import datetime
from pathlib import Path

from . import config
from .summarize import KST, read_front_matter


def remove(video_ids: list[str], summaries: Path | None = None, state: Path | None = None,
           transcripts: Path | None = None) -> list[dict]:
    summaries = summaries or config.path("summaries")
    state = state or config.path("state")
    transcripts = transcripts or config.path("transcripts")
    state.mkdir(parents=True, exist_ok=True)
    done = []
    for vid in video_ids:
        files = list(summaries.glob(f"*/*_{vid}.md"))
        if not files:
            continue
        meta = read_front_matter(files[0])
        for f in files:
            f.unlink()
        (transcripts / f"{vid}.json").unlink(missing_ok=True)
        rec = {"video_id": vid, "title": meta.get("title", ""), "category": meta.get("category", ""),
               "lifespan": meta.get("lifespan", ""), "time": datetime.now(KST).isoformat(timespec="seconds")}
        with (state / "removed.jsonl").open("a") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done.append(rec)
    return done
