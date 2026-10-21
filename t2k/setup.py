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

from . import config, deploy, i18n, site, site_ops
from .i18n import t
from .site_ops import ANSI, WRANGLER

# init 이 만드는 파일. 주석은 init 의 언어(T2K_LANG, 없으면 영어)로 쓴다
GITIGNORE = {
    "ko": """# 생성물 · 처리 상태 · 자막은 커밋하지 않는다
users/*/site/
users/*/state/
state/
transcripts/
.DS_Store
""",
    "en": """# Generated sites, processing state and captions are not committed
users/*/site/
users/*/state/
state/
transcripts/
.DS_Store
""",
}

CONFIG = {
    "ko": """# t2k 설정. 기본값은 t2k/defaults.toml 에 있고, 같은 표 · 키를 여기에 쓰면 덮어쓴다.

[general]
# 사이트 화면 · 요약 · 주간 요약 · 디스코드 답장 · 명령줄의 언어. "en" (English) 또는 "ko" (한국어)
# 사람을 추가하기 전에 정한다: 사람의 첫 카테고리와 관심사 파일이 이 언어로 만들어진다
language = "{language}"

[site]
# Cloudflare 계정의 workers.dev 하위 도메인 (대시보드 → Workers & Pages). 아래 따옴표 안에 쓴다.
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
""",
    "en": """# t2k settings. Defaults live in t2k/defaults.toml; set the same table and key here to override them.

[general]
# Language of the sites, summaries, weekly reviews, Discord replies and command line: "en" (English) or "ko" (Korean).
# Choose it before adding people: their first category and interests file are created in this language.
language = "{language}"

[site]
# Your Cloudflare account's workers.dev subdomain (dashboard → Workers & Pages). Write it inside the quotes below.
# Sites live at https://<id>-yt.<subdomain>.workers.dev
subdomain = ""

# Add people with `t2k user add`. Format:
# [[user]]
# id = "alice"                 # lowercase letters and digits; used for the folder and Worker name
# name = "Alice"               # name shown on the site
# channel_id = "123..."        # this person's Discord channel
# owners = ["456..."]          # Discord user IDs allowed to make requests in this channel
# kv_namespace = "..."         # site edit queue (created by user add)
# ready = false                # set to true by t2k user ready once Access is confirmed
""",
}

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
    code = i18n.lang()  # config.toml 이 아직 없으면 T2K_LANG, 그것도 없으면 영어
    for name, text in (("config.toml", CONFIG[code].replace("{language}", code)), (".gitignore", GITIGNORE[code])):
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
    """사람을 추가한다. 도중에 실패하면 같은 명령을 다시 실행해 이어 간다:
    config.toml 에 이미 있고 아직 열지 않은 사람(ready = false)이면 기록된 KV 를 그대로 쓰고 안내 페이지부터 다시 올린다."""
    if not config.USER_ID_RE.match(user_id):
        log(t("setup.bad_id"))
        return 2
    existing = next((u for u in config.users() if u.id == user_id), None)
    if existing and existing.ready:
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
    if existing and existing.kv_namespace:
        log(t("setup.resume", id=user_id))
        if existing.channel_id != channel_id or list(existing.owners) != owners or existing.name != name:
            log(t("setup.resume_keeps", path=config.config_file()))
        kv = existing.kv_namespace
    else:
        try:
            kv = create_kv(user_id)
        except RuntimeError as e:
            log(t("setup.kv_failed", error=e))
            return 1
        with config.config_file().open("a") as f:
            f.write(user_block({"id": user_id, "name": name, "channel_id": channel_id, "owners": owners, "kv_namespace": kv}))
        config.reload()
    log(t("setup.kv", id=kv))
    u = config.user(user_id)
    try:
        deploy.placeholder(u)
    except RuntimeError as e:  # 설정에는 이미 기록했다. 같은 명령을 다시 실행하면 여기서부터 이어 간다
        log(t("setup.placeholder_failed", error=e))
        return 1
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


def clear_queue(u: config.User, log=print) -> None:
    """사이트 조작 대기열(KV)을 비운다. Access 를 설정하기 전에는 누구나 사이트의 조작 API 에 요청을 넣을 수 있었으므로,
    사이트를 열기 전에 쌓인 조작은 적용하지 않고 버린다."""
    with config.as_user(u):
        items = site_ops.pending()
        for x in items:
            site_ops.delete(x["key"])
    if items:
        log(t("setup.queue_cleared", n=len(items)))


def ready(user_id: str, log=print) -> int:
    u = config.user(user_id)
    ok, why = access_protected(u.site_url)
    log(f"{u.site_url}: {why}")
    if not ok:
        log(t("setup.not_ready"))
        return 1
    try:
        clear_queue(u, log=log)
    except RuntimeError as e:
        log(t("setup.queue_failed", error=e))
        return 1
    set_ready(user_id)
    _commit(t("setup.commit_ready", name=u.name), "config.toml", log=log)
    u = config.user(user_id)
    with config.as_user(None):
        common = config.path("state")
    with config.as_user(u):
        site.build(status_dir=common)
    try:
        deploy.worker(u)
    except RuntimeError as e:  # ready 는 이미 기록했다. 다음 파이프라인 주기 또는 t2k redeploy 로 다시 올린다
        log(t("setup.deploy_failed", error=e, id=user_id))
        return 1
    log(t("setup.ready_done", url=u.site_url))
    return 0


def check_site(user_id: str) -> int:
    """사이트가 생성되는지만 확인한다 (agent 가 지시를 적용하기 전에 쓴다). 결과물은 버린다."""
    u = config.user(user_id)
    with config.as_user(u), tempfile.TemporaryDirectory() as d:
        site.build(out=Path(d) / "site")
    return 0
