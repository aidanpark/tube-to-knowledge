"""설정 로드, 사람별 경로, 카테고리 추가 · 삭제.

코드와 데이터를 분리한다.
- 코드(CODE): 이 패키지. 사이트 템플릿 · 프롬프트 · Worker · 기본 설정(defaults.toml).
- 데이터(DATA): 환경 변수 T2K_HOME 의 폴더 (기본값 ~/t2k-data). git 저장소로 둔다 (t2k init).

    <DATA>/config.toml            사람 목록([[user]])과 기본 설정을 덮어쓰는 값
    <DATA>/users/<id>/            사람별 파일
        categories.toml           카테고리
        profile.md · rules.md     관심사 · 요약 방식 요청
        templates/ · style.css    사이트 템플릿 덮어쓰기 · 덧붙일 CSS
        summaries/                요약 (<카테고리>/<날짜>_<동영상ID>.md)
        weekly/                   주간 요약 (<YYYY-Www>/<카테고리 id>.json)
        site/ · state/            생성한 사이트 · 처리 상태 (커밋하지 않는다)
    <DATA>/state/                 사람과 관계없는 상태 (status.json · 잠금 · agent.json)
    <DATA>/transcripts/           자막 (커밋하지 않는다)

as_user(u) 안에서는 path() · categories() 가 그 사람의 파일을 가리킨다.
as_user 밖에서 요약 · 사이트 · 카테고리는 첫 번째 사람의 것을, state 는 공통 폴더를 가리킨다.
"""
import json
import os
import re
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CODE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("T2K_HOME", "~/t2k-data")).expanduser().resolve()
OTHER_ID = "0099"  # 기타: 맞는 카테고리가 없을 때 쓴다. 삭제할 수 없다
# 카테고리 이름은 폴더 이름(<id>_<이름>)이 되므로 경로 · 파일 이름에 쓸 수 없는 문자를 막는다
NAME_RE = re.compile(r'^[^/\\:*?"<>|\x00-\x1f]{1,20}$')
SCOPE_MAX = 150
USER_ID_RE = re.compile(r"^[a-z][a-z0-9]{0,19}$")


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    scope: str

    @property
    def key(self) -> str:
        return f"{self.id}_{self.name}"


@dataclass(frozen=True)
class User:
    id: str              # 영문 소문자 · 숫자. 폴더 이름 · Worker 이름에 쓴다
    name: str            # 화면에 쓰는 이름
    channel_id: str      # 디스코드 채널. 이 채널에 올린 링크가 이 사람의 대기열이다
    kv_namespace: str = ""  # 사이트 조작 대기열 KV (t2k user add 가 만든다)
    owners: tuple[str, ...] = ()  # 이 채널에서 지시(링크가 아닌 글)를 받을 디스코드 사용자 ID (agent.py)
    ready: bool = False  # Access 를 설정 · 확인했다. false 이면 사이트를 배포하지 않는다 (t2k user ready)
    worker: str = ""     # Cloudflare Worker 이름. 비어 있으면 <id>-yt
    site_url: str = ""   # 비어 있으면 https://<worker>.<[site] subdomain>.workers.dev

    def __post_init__(self):
        if not USER_ID_RE.match(self.id):
            raise ValueError(f"사람 id 는 영문 소문자로 시작하는 소문자 · 숫자 20자 이내: {self.id!r}")
        object.__setattr__(self, "owners", tuple(self.owners))
        if not self.worker:
            object.__setattr__(self, "worker", f"{self.id}-yt")
        if not self.site_url:
            sub = settings()["site"].get("subdomain", "")
            object.__setattr__(self, "site_url", f"https://{self.worker}.{sub}.workers.dev" if sub else "")

    @property
    def custom(self) -> Path:
        return DATA / "users" / self.id

    def file(self, name: str) -> Path:
        """summaries · site · state · categories.toml · profile.md · rules.md · templates · style.css 의 경로."""
        return self.custom / name

    def owned(self) -> dict[str, Path]:
        """디스코드 지시(agent.py)로 바꿀 수 있는 이 사람의 파일 · 폴더. 이 밖은 지시로 바꾸지 않는다."""
        return {name: self.file(name) for name in OWNED}


OWNED = ("profile.md", "rules.md", "templates", "style.css", "categories.toml", "summaries")

_current: User | None = None


def config_file() -> Path:
    return DATA / "config.toml"


@lru_cache
def _load() -> dict:
    f = config_file()
    if not f.exists():
        from .i18n import t  # i18n 이 이 모듈을 가져오므로 여기서 가져온다
        raise SystemExit(t("config.no_config", path=f))
    return tomllib.loads(f.read_text())


@lru_cache
def settings() -> dict:
    """기본 설정(defaults.toml)에 데이터 폴더 config.toml 의 같은 표(section) 값을 덮어쓴다."""
    merged = tomllib.loads((CODE / "defaults.toml").read_text())
    for section, values in _load().items():
        if isinstance(values, dict):
            merged.setdefault(section, {}).update(values)
    return merged


@lru_cache
def users() -> tuple[User, ...]:
    return tuple(User(**u) for u in _load().get("user", []))


def user(user_id: str) -> User:
    u = next((u for u in users() if u.id == user_id), None)
    if u is None:
        from .i18n import t
        raise SystemExit(t("config.unknown_user", id=user_id))
    return u


def default_user() -> User:
    if not users():
        from .i18n import t
        raise SystemExit(t("config.no_users", path=config_file()))
    return users()[0]


def reload() -> None:
    """설정 파일을 다시 읽는다 (t2k user add · 시험)."""
    for f in (_load, settings, users):
        f.cache_clear()


@contextmanager
def as_user(u: User | None):
    """u 의 경로 · 카테고리를 쓴다. None 이면 공통 경로."""
    global _current
    prev, _current = _current, u
    try:
        yield u
    finally:
        _current = prev


def current_user() -> User | None:
    return _current


def load_categories(path: Path) -> tuple[Category, ...]:
    data = tomllib.loads(path.read_text())
    return tuple(Category(c["id"], c["name"], c["scope"]) for c in data["category"])


def categories_file() -> Path:
    return (_current or default_user()).file("categories.toml")


@lru_cache
def _categories(path: Path, mtime: float) -> tuple[Category, ...]:
    return load_categories(path)


def categories() -> tuple[Category, ...]:
    """지금 사용자의 카테고리. 파일이 바뀌면(사이트에서 추가 · 삭제) 다시 읽는다."""
    f = categories_file()
    return _categories(f, f.stat().st_mtime_ns)


categories.cache_clear = _categories.cache_clear  # 시험 · 기존 호출과의 호환


def category_by_key(key: str) -> Category | None:
    return next((c for c in categories() if c.key == key), None)


def other_category() -> Category:
    """맞는 카테고리가 없을 때 쓰는 카테고리 (id 0099). 이름은 언어에 따라 기타 · Other 등으로 다를 수 있다."""
    return next(c for c in categories() if c.id == OTHER_ID)


def path(name: str) -> Path:
    """summaries · weekly · site · state · transcripts 의 경로."""
    if name == "transcripts":
        return DATA / "transcripts"
    if name == "state" and not _current:
        return DATA / "state"
    if name in ("summaries", "weekly", "site", "state"):
        return (_current or default_user()).file(name)
    raise KeyError(name)


def _blocks(text: str) -> tuple[str, list[str]]:
    """categories.toml 을 머리말과 [[category]] 블록으로 나눈다. 블록의 주석을 보존하려고 글자 그대로 다룬다."""
    head, *rest = text.split("[[category]]")
    return head, ["[[category]]" + b for b in rest]


def add_category(name: str, scope: str, path: Path | None = None) -> Category:
    """새 카테고리를 기타(0099) 앞에 추가한다. ID 는 기존 최대값 + 1."""
    path = path or categories_file()
    name, scope = name.strip(), " ".join(scope.split())
    cats = load_categories(path)
    if not NAME_RE.match(name) or name.startswith("."):
        raise ValueError(f"쓸 수 없는 이름: {name!r}")
    if not scope or len(scope) > SCOPE_MAX:
        raise ValueError(f"설명은 1~{SCOPE_MAX}자: {len(scope)}자")
    if any(c.name == name for c in cats):
        raise ValueError(f"같은 이름이 있음: {name}")
    n = max((int(c.id) for c in cats if c.id != OTHER_ID), default=-1) + 1
    if n >= int(OTHER_ID):
        raise ValueError("카테고리 ID 가 모두 사용됨")
    cat = Category(f"{n:04d}", name, scope)
    block = (f"[[category]]\nid = {json.dumps(cat.id)}\nname = {json.dumps(name, ensure_ascii=False)}\n"
             f"scope = {json.dumps(scope, ensure_ascii=False)}\n\n")
    head, blocks = _blocks(path.read_text())
    i = next((i for i, b in enumerate(blocks) if f'id = "{OTHER_ID}"' in b), len(blocks))
    if i == len(blocks) and blocks and not blocks[-1].endswith("\n\n"):
        blocks[-1] = blocks[-1].rstrip("\n") + "\n\n"
    blocks.insert(i, block)
    path.write_text(head + "".join(blocks))
    categories.cache_clear()
    return cat


def delete_category(cat_id: str, path: Path | None = None) -> Category:
    """카테고리를 목록에서 뺀다. 그 카테고리의 요약은 호출하는 쪽이 먼저 옮긴다."""
    path = path or categories_file()
    if cat_id == OTHER_ID:
        raise ValueError("기타는 삭제할 수 없음")
    cat = next((c for c in load_categories(path) if c.id == cat_id), None)
    if not cat:
        raise ValueError(f"없는 카테고리: {cat_id}")
    head, blocks = _blocks(path.read_text())
    path.write_text(head + "".join(b for b in blocks if f'id = "{cat_id}"' not in b))
    categories.cache_clear()
    return cat
