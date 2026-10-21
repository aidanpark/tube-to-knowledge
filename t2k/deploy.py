"""사람별 요약 사이트를 Cloudflare Workers 에 올린다 (wrangler deploy).

주소는 <worker>.<계정 하위 도메인>.workers.dev 다. 사이트(site/)는 Workers 정적 파일(assets)로, 사이트 조작 API(/api/ops)는
worker/index.js 로 올린다. 설정 파일은 배포할 때마다 임시 폴더에 만든다: 그 사람의 Worker 이름 · 사이트 폴더 · 조작 대기열 KV.

사이트는 Cloudflare Access 로 보호한다 (그 사람과 관리자만). 새 Worker 는 안내 페이지(placeholder)로 먼저 만들고,
Access 가 적용된 것을 확인한 뒤에만 실제 사이트를 올린다. 버전별 미리보기 주소(preview_urls)는 Access 밖에
열릴 수 있으므로 끈다.
"""
import json
import subprocess
import tempfile
from pathlib import Path

from . import config, i18n
from .i18n import t
from .site_ops import ANSI, WRANGLER

COMPATIBILITY_DATE = "2026-10-01"


def worker_config(user: config.User, site_dir: Path) -> dict:
    if not (user.worker and user.kv_namespace):
        raise RuntimeError(f"Worker · KV 가 설정되지 않음: {user.id}")
    return {
        "name": user.worker,
        "main": str(config.CODE / "worker" / "index.js"),
        "compatibility_date": COMPATIBILITY_DATE,
        "workers_dev": True,
        "preview_urls": False,
        # html_handling none: v/<id>.html 을 그대로 응답한다 (기본값은 .html 을 뺀 주소로 다시 보낸다)
        "assets": {"directory": str(site_dir), "binding": "ASSETS", "html_handling": "none",
                   "not_found_handling": "none"},
        "kv_namespaces": [{"binding": "OPS", "id": user.kv_namespace}],
    }


def worker(user: config.User, site_dir: Path | None = None) -> None:
    """user 의 사이트를 올린다. site_dir 이 없으면 그 사람의 site/."""
    site_dir = Path(site_dir or user.file("site")).resolve()
    if not (site_dir / "index.html").exists():
        raise RuntimeError(f"사이트가 없음: {site_dir}")
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "wrangler.json"
        cfg.write_text(json.dumps(worker_config(user, site_dir), ensure_ascii=False, indent=1))
        # 빈 임시 폴더에서 실행한다. 다른 폴더의 site/ 를 자동으로 찾아 올리는 일이 없게 한다 (2026-10-20 사고)
        p = subprocess.run([*WRANGLER, "deploy", "--config", str(cfg)], capture_output=True, text=True,
                           timeout=300, cwd=d)
        if p.returncode != 0:
            raise RuntimeError(ANSI.sub("", p.stderr or p.stdout).strip()[-1500:])


def placeholder(user: config.User) -> None:
    """요약이 없는 안내 페이지만 올린다. 새 Worker 를 만들고 Access 를 설정하기 전에 쓴다."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "index.html").write_text(
            f'<!doctype html><html lang="{i18n.lang()}"><meta charset="utf-8"><meta name="robots" content="noindex">'
            f'<title>{t("setup.placeholder_title")}</title><p>{t("setup.placeholder_body")}</p>')  # Access 전에 공개되므로 이름 등 아무 정보도 넣지 않는다
        (Path(d) / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
        worker(user, Path(d))
