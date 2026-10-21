"""표시 언어: 사이트 화면 · 요약 · 주간 요약 · 디스코드 답장에 쓰는 언어.

설정: 데이터 폴더 config.toml 의 [general] language ("en" | "ko"). 기본값은 defaults.toml 의 "en".
정하는 순서 (lang): 환경 변수 T2K_LANG → config.toml → config.toml 이 없으면 "en" (t2k init 전의 CLI · doctor).
문구: t2k/locales/<언어>/*.toml. 파일은 영역별로 나눈다 (site · summary · discord · cli). 표의 키는 점으로 이어 붙인다:
    [home]
    title = "Summaries"      → t("home.title")
값의 {이름} 은 t(키, 이름=값) 으로 채운다 (str.format). 키가 없으면 영어 문구, 그것도 없으면 키를 그대로 반환한다.
"""
import os
import tomllib
from functools import lru_cache

from . import config

LANGS = ("en", "ko")


def lang() -> str:
    env = os.environ.get("T2K_LANG")
    if env:  # 데이터 폴더가 없을 때(t2k init 전)에도 언어를 정할 수 있다
        if env not in LANGS:
            raise SystemExit(f'T2K_LANG must be one of {", ".join(LANGS)} (T2K_LANG 은 {" · ".join(LANGS)} 중 하나): {env!r}')
        return env
    if not config.config_file().exists():  # config.toml 이 없으면 settings() 가 끝내 버리므로 먼저 확인한다
        return "en"
    v = config.settings().get("general", {}).get("language", "en")
    if v not in LANGS:
        raise SystemExit(f'[general] language must be one of {", ".join(LANGS)} '
                         f'([general] language 는 {" · ".join(LANGS)} 중 하나): {v!r}')
    return v


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


@lru_cache
def catalog(code: str) -> dict[str, str]:
    out = {}
    for f in sorted((config.CODE / "locales" / code).glob("*.toml")):
        out.update(_flatten(tomllib.loads(f.read_text())))
    return out


def t(key: str, _lang: str | None = None, **kw) -> str:
    code = _lang or lang()
    s = catalog(code).get(key)
    if s is None:
        s = catalog("en").get(key, key)
    return s.format(**kw) if kw else s


def table(prefix: str, _lang: str | None = None) -> dict[str, str]:
    """prefix 아래의 문구 전부 ({하위 키: 문구}). 사이트의 JS 에 넘기거나 값 → 표시 이름 대응에 쓴다."""
    code = _lang or lang()
    merged = {**{k: v for k, v in catalog("en").items() if k.startswith(prefix + ".")},
              **{k: v for k, v in catalog(code).items() if k.startswith(prefix + ".")}}
    return {k[len(prefix) + 1:]: v for k, v in merged.items()}
