"""시험용 데이터 폴더. 모든 시험은 임시 데이터 폴더(config.DATA)에서 실행한다: 실제 데이터 폴더를 읽거나 바꾸지 않는다.

사람 두 명: alice (카테고리 여러 개, 관심사 AI 개발 도구) · bob (기타 하나, 관심사 요리와 여행).
"""
import pytest

from ytbook import config

CONFIG = """
[site]
subdomain = "example"

[[user]]
id = "alice"
name = "앨리스"
channel_id = "100"
site_url = "https://a.example"
worker = "a"
kv_namespace = "kv-a"
ready = true
owners = ["admin"]

[[user]]
id = "bob"
name = "밥"
channel_id = "200"
site_url = "https://s.example"
worker = "s"
kv_namespace = "kv-s"
ready = true
owners = ["bobuser", "admin"]
"""

ALICE_CATEGORIES = """# 시험용 카테고리

[[category]]
id = "0001"
name = "AI_개발도구"
scope = "AI 코딩 도구, 에이전트, 개발 환경"

[[category]]
id = "0002"
name = "AI_뉴스"
scope = "AI 업계 소식, 모델 발표"

[[category]]
id = "0010"
name = "음악"
scope = "노래, 공연, 뮤직비디오"

[[category]]
id = "0099"
name = "기타"
scope = "위 어디에도 맞지 않음"
"""

OTHER_ONLY = '[[category]]\nid = "0099"\nname = "기타"\nscope = "위 어디에도 맞지 않음"\n'


def make_data(root, config_text: str = CONFIG):
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.toml").write_text(config_text)
    a = root / "users" / "alice"
    a.mkdir(parents=True, exist_ok=True)
    (a / "categories.toml").write_text(ALICE_CATEGORIES)
    (a / "profile.md").write_text("- AI 개발 도구와 업무 자동화")
    b = root / "users" / "bob"
    b.mkdir(parents=True, exist_ok=True)
    (b / "categories.toml").write_text(OTHER_ONLY)
    (b / "profile.md").write_text("- 요리와 여행")
    return root


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    root = make_data(tmp_path / "data")
    monkeypatch.setattr(config, "DATA", root)
    config.reload()
    config.categories.cache_clear()
    yield root
    config.reload()
    config.categories.cache_clear()
