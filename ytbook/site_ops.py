"""사이트에서 한 조작(Cloudflare KV 의 조작 대기열)을 이 컴퓨터에서 반영한다.

조작은 사이트의 버튼 → Worker 의 /api/ops (worker/index.js) → KV 에 쌓인다. 종류는 네 가지다:
- remove: 요약 삭제 (ytbook remove 와 같다)
- move: 요약의 카테고리 이동 (ytbook move 와 같다)
- cat-create: 카테고리 추가 (이름 · 설명. ID 는 여기서 정한다)
- cat-delete: 카테고리 삭제 (남은 요약은 기타로 옮긴다)

1. KV 의 대기열을 읽는다 (wrangler, 한 번 로그인: npx wrangler login). 키가 시각 순이므로 조작한 순서대로 반영한다.
2. 그 사람의 요약 파일 · categories.toml 을 고친다.
3. 사이트를 다시 생성하고 커밋 · 푸시한 뒤 Worker 에 올린다.
4. 푸시에 성공한 뒤에만 KV 에서 그 항목을 지운다. 그 전까지 사이트는 대기 중인 조작을 미리 적용해 보여 준다.
"""
import json
import re
import subprocess
import time

from . import config, recategorize, remove, site

WRANGLER = ["npx", "--yes", "wrangler@4.147.0"]
ANSI = re.compile(r"\x1b\[[0-9;]*m")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# wrangler 가 OAuth 토큰을 갱신(약 1시간마다)한 직후의 요청은 401 「Authentication error [code: 10000]」 로
# 실패하고, 몇 초 뒤의 요청은 성공한다 (2026-10-19 ~ 20 실패 9건 모두 갱신 직후). 이 경우에만 한 번 더 시도한다
AUTH_RETRY_AFTER = 5


def namespace_id() -> str:
    """지금 사용자(as_user 밖이면 첫 번째 사람)의 조작 대기열 KV."""
    return (config.current_user() or config.default_user()).kv_namespace


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=180, cwd=config.DATA)


def _error(p: subprocess.CompletedProcess) -> str:
    return ANSI.sub("", p.stderr or p.stdout).strip()


def _wrangler(*args: str) -> str:
    cmd = [*WRANGLER, "kv", *args, "--namespace-id", namespace_id(), "--remote"]
    p = _run(cmd)
    if p.returncode != 0 and "[code: 10000]" in _error(p):
        time.sleep(AUTH_RETRY_AFTER)
        p = _run(cmd)
    if p.returncode != 0:
        raise RuntimeError(f"wrangler 실패: {_error(p)}")
    return p.stdout


def parse(key: dict) -> dict:
    """KV 키 하나를 조작으로 바꾼다. 이전 삭제 버튼의 키(동영상 ID, type 없음)는 remove 로 읽는다."""
    meta = key.get("metadata") or {}
    if "type" not in meta and ID_RE.match(key["name"]):
        return {"key": key["name"], "type": "remove", "id": key["name"], **meta}
    return {"key": key["name"], **meta}


def pending() -> list[dict]:
    return [parse(k) for k in json.loads(_wrangler("key", "list"))]


def delete(key: str) -> None:
    _wrangler("key", "delete", key)


def _cat(cat_id: str) -> config.Category | None:
    return next((c for c in config.categories() if c.id == cat_id), None)


def apply(ops: list[dict], log=print) -> list[str]:
    """조작을 순서대로 반영하고 바뀐 것을 한 줄씩 반환한다. 반영할 수 없는 조작은 기록만 하고 건너뛴다."""
    changes = []
    for op in sorted(ops, key=lambda o: o["key"]):
        t = op.get("type")
        try:
            if t == "remove":
                for r in remove.remove([op["id"]]):
                    changes.append(f"삭제 {r['video_id']} {r['title']}")
            elif t == "move":
                cat = _cat(op.get("to", ""))
                if not cat:
                    raise ValueError(f"없는 카테고리: {op.get('to')}")
                for line in recategorize.move([op["id"]], cat.key):
                    changes.append(f"이동 {line} → {cat.name}")
            elif t == "cat-create":
                cat = config.add_category(op.get("name", ""), op.get("scope", ""))
                changes.append(f"카테고리추가 {cat.key}")
            elif t == "cat-delete":
                cat = _cat(op.get("cat", ""))
                if not cat or cat.id == config.OTHER_ID:
                    raise ValueError(f"삭제할 수 없는 카테고리: {op.get('cat')}")
                folder = config.path("summaries") / cat.key
                vids = [p.stem.split("_", 1)[1] for p in folder.glob("*.md")]
                moved = recategorize.move(vids, config.category_by_key(f"{config.OTHER_ID}_기타").key) if vids else []
                config.delete_category(cat.id)
                if folder.exists() and not any(folder.iterdir()):
                    folder.rmdir()
                changes.append(f"카테고리삭제 {cat.key} (요약 {len(moved)}편 → 기타)")
            else:
                raise ValueError(f"모르는 조작: {t}")
        except (ValueError, KeyError) as e:
            log(f"조작 건너뜀 ({op['key']} {t}): {e}")
    return changes


def run(log=print) -> int:
    """ytbook sync-ops: 파이프라인을 기다리지 않고 지금 사용자(as_user)의 대기열을 바로 반영한다."""
    from . import deploy
    from .pipeline import git_publish

    items = pending()
    if not items:
        log("조작 대기 없음")
        return 0
    changes = apply(items, log)
    for c in changes:
        log(c)
    with config.as_user(None):
        common = config.path("state")
    site.build(status_dir=common)
    msg = f"사이트 조작 {len(changes)}건\n\n" + "\n".join(f"- {c}" for c in changes)
    if not git_publish(msg):
        raise RuntimeError("커밋 · 푸시 실패 (KV 는 그대로 둔다)")
    u = config.current_user() or config.default_user()
    if u.ready:
        deploy.worker(u)
    for x in items:  # 배포가 시작된 뒤에 대기열에서 지운다. 반영할 수 없던 조작도 정리한다
        delete(x["key"])
    log(f"처리 {len(items)}건 (변경 {len(changes)}건)")
    return len(items)
