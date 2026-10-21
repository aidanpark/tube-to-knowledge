"""사이트 조작 대기열: wrangler 인증 오류 재시도와 오류 원문. wrangler 는 호출하지 않는다."""
import json
import subprocess

import pytest

from ytbook import site_ops

AUTH_ERR = "\x1b[31m✘ \x1b[41;31m[ERROR]\x1b[0m A request to the Cloudflare API (/keys) failed.\n\n  Authentication error [code: 10000]\n"


def fake_runs(monkeypatch, *results):
    calls = []

    def run(args):
        calls.append(args)
        code, out, err = results[len(calls) - 1]
        return subprocess.CompletedProcess(args, code, out, err)

    monkeypatch.setattr(site_ops, "_run", run)
    monkeypatch.setattr(site_ops, "namespace_id", lambda: "ns")
    monkeypatch.setattr(site_ops.time, "sleep", lambda s: None)
    return calls


def test_retries_once_after_auth_error(monkeypatch):
    calls = fake_runs(monkeypatch, (1, "", AUTH_ERR), (0, '[{"name": "abcdefghijk"}]', ""))
    assert site_ops.pending() == [{"key": "abcdefghijk", "type": "remove", "id": "abcdefghijk"}]
    assert len(calls) == 2


def test_other_errors_not_retried_and_kept_whole(monkeypatch):
    err = "\x1b[31mX\x1b[0m " + "가" * 500 + " 끝"
    calls = fake_runs(monkeypatch, (1, "", err))
    with pytest.raises(RuntimeError) as e:
        site_ops.pending()
    assert len(calls) == 1
    assert "\x1b" not in str(e.value) and str(e.value).endswith(" 끝") and "가" * 500 in str(e.value)


def test_auth_error_twice_raises(monkeypatch):
    calls = fake_runs(monkeypatch, (1, "", AUTH_ERR), (1, "", AUTH_ERR))
    with pytest.raises(RuntimeError, match=r"Authentication error \[code: 10000\]"):
        site_ops.pending()
    assert len(calls) == 2


def test_parse_new_keys():
    k = {"name": "000001792490000-1a2b3c4d", "metadata": {"type": "move", "id": "abcdefghijk", "to": "0003"}}
    assert site_ops.parse(k) == {"key": k["name"], "type": "move", "id": "abcdefghijk", "to": "0003"}


CATS = """# 머리말

[[category]]
id = "0000"
name = "요리"
scope = "레시피"  # 주석은 남는다

[[category]]
id = "0099"
name = "기타"
scope = "위 어디에도 맞지 않음"
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """임시 categories.toml 과 summaries/ 로 config 를 바꾼다."""
    from ytbook import config
    cats = tmp_path / "categories.toml"
    cats.write_text(CATS)
    summaries = tmp_path / "summaries"
    monkeypatch.setattr(config, "categories_file", lambda: cats)
    monkeypatch.setattr(config, "path", lambda name: tmp_path / name)
    config.categories.cache_clear()
    yield tmp_path
    config.categories.cache_clear()


def summary(root, key, vid):
    from tests.test_summarize import FULL, TR
    from ytbook.summarize import render, validate
    d = root / "summaries" / key
    d.mkdir(parents=True, exist_ok=True)
    s = validate({**json.loads(json.dumps(FULL)), "category": key}, short=False)
    (d / f"2026-10-20_{vid}.md").write_text(render({**TR, "video_id": vid}, {**s, "category": key}, "2026-10-20"))


def test_apply_create_move_delete_in_order(repo):
    from ytbook import config
    summary(repo, "0099_기타", "AAAAAAAAAAA")
    ops = [  # 키 순서대로 반영한다: 추가 → 이동 → 이전 카테고리 삭제
        {"key": "3", "type": "cat-delete", "cat": "0000"},
        {"key": "1", "type": "cat-create", "name": "여행", "scope": "국내  여행,\n캠핑"},
        {"key": "2", "type": "move", "id": "AAAAAAAAAAA", "to": "0001"},
    ]
    summary(repo, "0000_요리", "BBBBBBBBBBB")
    changes = site_ops.apply(ops, log=lambda m: None)
    assert [c.split()[0] for c in changes] == ["카테고리추가", "이동", "카테고리삭제"]
    assert [(c.id, c.name, c.scope) for c in config.categories()] == [("0001", "여행", "국내 여행, 캠핑"), ("0099", "기타", "위 어디에도 맞지 않음")]
    assert (repo / "summaries/0001_여행/2026-10-20_AAAAAAAAAAA.md").exists()
    assert (repo / "summaries/0099_기타/2026-10-20_BBBBBBBBBBB.md").exists()
    assert not (repo / "summaries/0000_요리").exists()
    assert "# 머리말" in (repo / "categories.toml").read_text()


def test_apply_skips_invalid(repo):
    from ytbook import config
    logs = []
    ops = [{"key": "1", "type": "cat-create", "name": "요리", "scope": "중복"},
           {"key": "2", "type": "cat-create", "name": "a/b", "scope": "경로 문자"},
           {"key": "3", "type": "cat-delete", "cat": "0099"},
           {"key": "4", "type": "move", "id": "AAAAAAAAAAA", "to": "0042"},
           {"key": "5", "type": "unknown"}]
    assert site_ops.apply(ops, log=logs.append) == []
    assert len(logs) == 5
    assert "주석은 남는다" in (repo / "categories.toml").read_text() and len(config.categories()) == 2
