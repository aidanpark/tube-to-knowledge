"""설치 명령: 데이터 폴더 만들기(init), 사람 추가(user add), 사이트 열기(user ready), 사이트 생성 확인(check-site).

사람을 추가하는 순서 (README 의 설치 안내):
1. t2k user add: 사람 폴더 · 카테고리(기타 하나) · 조작 대기열 KV 를 만들고, 안내 페이지만 있는 Worker 를 올린다.
   이때 사이트 주소가 생기지만 요약은 아직 올리지 않는다 (ready = false).
2. Cloudflare 대시보드에서 그 Worker 에 Access 를 설정한다.
3. t2k user ready: 사이트가 Access 로그인 화면으로 보내는지 확인한 뒤에만 ready = true 로 바꾸고 요약을 올린다.
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

import requests

from . import config, deploy, i18n, site
from .i18n import t
from .site_ops import ANSI, WRANGLER

GITIGNORE = """# 생성물 · 처리 상태 · 자막은 커밋하지 않는다
users/*/site/
users/*/state/
state/
transcripts/
.DS_Store
"""

CONFIG = """# t2k 설정. 기본값은 t2k/defaults.toml 에 있고, 같은 표 · 키를 여기에 쓰면 덮어쓴다.

[general]
# 사이트 화면 · 요약 · 주간 요약 · 디스코드 답장의 언어. "en" (English) 또는 "ko" (한국어)
language = "en"

[site]
# Cloudflare 계정의 workers.dev 하위 도메인 (대시보드 → Workers & Pages → 오른쪽의 workers.dev 하위 도메인).
# 사이트 주소는 https://<id>-yt.<subdomain>.workers.dev 가 된다
subdomain = ""

# 사람은 `t2k user add` 로 추가한다. 형식:
# [[user]]
# id = "alice"                 # 영문 소문자 · 숫자. 폴더 · Worker 이름에 쓴다
# name = "앨리스"               # 화면에 쓰는 이름
# channel_id = "123..."        # 이 사람의 디스코드 채널
# owners = ["456..."]          # 이 채널에서 지시를 받을 디스코드 사용자 ID
# kv_namespace = "..."         # 사이트 조작 대기열 (user add 가 만든다)
# ready = false                # Access 를 확인한 뒤 user ready 가 true 로 바꾼다
"""

# 사람을 추가할 때 만드는 파일. [general] language 에 맞는 것을 쓴다
CATEGORIES = {
    "ko": """# 요약 카테고리. 사이트의 카테고리 화면에서 추가 · 삭제한다.
# Claude 는 이 목록에서만 선택한다. 맞는 것이 없으면 0099 로 분류하고 categoryProposal 에 제안을 남긴다.

[[category]]
id = "0099"
name = "기타"
scope = "위 어디에도 맞지 않음"
""",
    "en": """# Summary categories. Add or delete them on the site's Categories page.
# Claude chooses only from this list. If nothing fits, it uses 0099 and leaves a suggestion in categoryProposal.

[[category]]
id = "0099"
name = "Other"
scope = "Does not fit any category above"
""",
}

PROFILE = {
    "ko": """# 관심사

요약의 개인화 항목(시청 권유 · 개인 인사이트)에 쓰인다. 자유롭게 고친다. 디스코드로 고쳐 달라고 해도 된다.

- 직업 · 하는 일:
- 관심 분야:
- 요약에서 특히 알고 싶은 것:
""",
    "en": """# Interests

Used for the personalized parts of each summary (recommendation, personal insight). Edit freely, or ask on Discord.

- Job / what I do:
- Interests:
- What I most want from a summary:
""",
}


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd or config.DATA, timeout=60)


def _commit(message: str, *paths: str, log=print) -> None:
    _git("add", "-A", *paths)
    if _git("diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return
    c = _git("commit", "-q", "-m", message, "--", *paths)
    if c.returncode != 0:
        log(t("setup.commit_failed", error=c.stderr.strip()))


def init(log=print) -> int:
    """데이터 폴더를 만든다. 이미 있는 파일은 바꾸지 않는다."""
    d = config.DATA
    d.mkdir(parents=True, exist_ok=True)
    for name, text in (("config.toml", CONFIG), (".gitignore", GITIGNORE)):
        f = d / name
        if f.exists():
            log(t("setup.exists", path=f))
        else:
            f.write_text(text)
            log(t("setup.created", path=f))
    for sub in ("users", "state"):
        (d / sub).mkdir(exist_ok=True)
    if _git("rev-parse", "--git-dir").returncode != 0:
        _git("init", "-q", "-b", "main")
        log(t("setup.git_init", path=d))
    _commit(t("setup.commit_init"), "config.toml", ".gitignore", log=log)
    log(t("setup.init_done", path=d))
    return 0


def _wrangler(*args: str) -> str:
    # 빈 임시 폴더에서 실행한다. 다른 폴더의 설정 · site/ 를 자동으로 찾아 쓰는 일이 없게 한다 (deploy.py)
    with tempfile.TemporaryDirectory() as d:
        p = subprocess.run([*WRANGLER, *args], capture_output=True, text=True, timeout=300, cwd=d)
    if p.returncode != 0:
        raise RuntimeError(t("setup.wrangler_failed", cmd=" ".join(args[:3]), error=ANSI.sub("", p.stderr or p.stdout).strip()[-800:]))
    return ANSI.sub("", p.stdout)


def create_kv(user_id: str) -> str:
    out = _wrangler("kv", "namespace", "create", f"t2k-{user_id}-ops")
    m = re.search(r"\b([0-9a-f]{32})\b", out)
    if not m:
        raise RuntimeError(t("setup.kv_not_found", out=out[-400:]))
    return m.group(1)


def user_block(u: dict) -> str:
    q = lambda s: json.dumps(s, ensure_ascii=False)
    return (f"\n[[user]]\nid = {q(u['id'])}\nname = {q(u['name'])}\nchannel_id = {q(u['channel_id'])}\n"
            f"owners = [{', '.join(q(o) for o in u['owners'])}]\nkv_namespace = {q(u['kv_namespace'])}\nready = false\n")


def add_user(user_id: str, name: str, channel_id: str, owners: list[str], log=print) -> int:
    if not config.USER_ID_RE.match(user_id):
        log(t("setup.bad_id"))
        return 2
    if any(u.id == user_id for u in config.users()):
        log(t("setup.user_exists", id=user_id))
        return 2
    if not re.fullmatch(r"\d{15,21}", channel_id) or not all(re.fullmatch(r"\d{15,21}", o) for o in owners):
        log(t("setup.bad_ids"))
        return 2
    if not config.settings()["site"].get("subdomain"):
        log(t("setup.no_subdomain", path=config.config_file()))
        return 2
    folder = config.DATA / "users" / user_id
    folder.mkdir(parents=True, exist_ok=True)
    for fname, text in (("categories.toml", CATEGORIES[i18n.lang()]), ("profile.md", PROFILE[i18n.lang()])):
        if not (folder / fname).exists():
            (folder / fname).write_text(text)
    log(t("setup.folder", path=folder))
    kv = create_kv(user_id)
    log(t("setup.kv", id=kv))
    with config.config_file().open("a") as f:
        f.write(user_block({"id": user_id, "name": name, "channel_id": channel_id, "owners": owners, "kv_namespace": kv}))
    config.reload()
    u = config.user(user_id)
    deploy.placeholder(u)
    _commit(t("setup.commit_add", name=name), "config.toml", f"users/{user_id}", log=log)
    log(t("setup.add_done", url=u.site_url, worker=u.worker, id=user_id))
    return 0


def access_protected(url: str) -> tuple[bool, str]:
    """주소가 Cloudflare Access 로그인 화면으로 보내는가. (보호됨, 설명)."""
    try:
        r = requests.get(url, allow_redirects=False, timeout=20)
    except requests.RequestException as e:
        return False, t("setup.connect_failed", error=e)
    loc = r.headers.get("location", "")
    if r.status_code in (302, 303) and "cloudflareaccess.com" in loc:
        return True, t("setup.protected", status=r.status_code)
    return False, t("setup.not_protected", status=r.status_code, loc=" → " + loc if loc else "")


def set_ready(user_id: str, value: bool = True) -> None:
    """config.toml 에서 그 사람 블록의 ready 값을 바꾼다. 주석 · 순서를 보존하려고 글자 그대로 다룬다."""
    f = config.config_file()
    head, *blocks = f.read_text().split("[[user]]")
    for i, b in enumerate(blocks):
        if re.search(rf'^id\s*=\s*"{re.escape(user_id)}"', b, re.M):
            new = f"ready = {'true' if value else 'false'}"
            blocks[i] = re.sub(r"^ready\s*=\s*\w+", new, b, count=1, flags=re.M) if re.search(r"^ready\s*=", b, re.M) \
                else b.rstrip("\n") + f"\n{new}\n\n"
            break
    else:
        raise SystemExit(t("setup.unknown_user", id=user_id))
    f.write_text("[[user]]".join([head, *blocks]))
    config.reload()


def ready(user_id: str, log=print) -> int:
    u = config.user(user_id)
    ok, why = access_protected(u.site_url)
    log(f"{u.site_url}: {why}")
    if not ok:
        log(t("setup.not_ready"))
        return 1
    set_ready(user_id)
    _commit(t("setup.commit_ready", name=u.name), "config.toml", log=log)
    u = config.user(user_id)
    with config.as_user(None):
        common = config.path("state")
    with config.as_user(u):
        site.build(status_dir=common)
    deploy.worker(u)
    log(t("setup.ready_done", url=u.site_url))
    return 0


def check_site(user_id: str) -> int:
    """사이트가 생성되는지만 확인한다 (agent 가 지시를 적용하기 전에 쓴다). 결과물은 버린다."""
    u = config.user(user_id)
    with config.as_user(u), tempfile.TemporaryDirectory() as d:
        site.build(out=Path(d) / "site")
    return 0
