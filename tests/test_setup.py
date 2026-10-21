"""설치 명령: 설정 병합, 데이터 폴더 만들기, 사이트 열기 확인, 자동 실행 설정 생성. wrangler · launchctl · 네트워크는 호출하지 않는다."""
import os
import plistlib
import subprocess
import sys

import pytest

from t2k import config, service, setup


def test_settings_merge_and_site_url(data):
    (data / "config.toml").write_text('[summary]\nmodel = "haiku"\n\n[site]\nsubdomain = "me"\n\n'
                                      '[[user]]\nid = "carol"\nname = "캐럴"\nchannel_id = "1"\n')
    config.reload()
    s = config.settings()
    assert s["summary"]["model"] == "haiku" and s["summary"]["timeout_seconds"] == 600  # 나머지는 기본값
    u = config.users()[0]
    assert u.worker == "carol-yt" and u.site_url == "https://carol-yt.me.workers.dev" and not u.ready
    assert config.path("summaries") == data / "users/carol/summaries"
    with pytest.raises(ValueError):
        config.User(id="Bad-Id", name="x", channel_id="1")


def test_init_creates_repo_and_keeps_existing(tmp_path, monkeypatch):
    d = tmp_path / "new"
    monkeypatch.setattr(config, "DATA", d)
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(k, v)
    assert setup.init(log=lambda m: None) == 0
    assert (d / "config.toml").exists() and "users/*/state/" in (d / ".gitignore").read_text()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True)
    assert head.returncode == 0
    (d / "config.toml").write_text("# 내 설정\n")
    setup.init(log=lambda m: None)
    assert (d / "config.toml").read_text() == "# 내 설정\n"


def test_add_user_appends_block_and_deploys_placeholder(data, monkeypatch):
    monkeypatch.setattr(setup, "create_kv", lambda uid: "f" * 32)
    deployed = []
    monkeypatch.setattr(setup.deploy, "placeholder", lambda u: deployed.append(u.worker))
    monkeypatch.setattr(setup, "_commit", lambda *a, **k: None)
    assert setup.add_user("carol", "캐럴", "123456789012345678", ["223456789012345678"], log=lambda m: None) == 0
    u = config.user("carol")
    assert u.kv_namespace == "f" * 32 and u.owners == ("223456789012345678",) and not u.ready
    assert deployed == ["carol-yt"] and (data / "users/carol/categories.toml").exists()
    assert setup.add_user("carol", "캐럴", "123456789012345678", ["1" * 18], log=lambda m: None) == 2  # 중복
    assert setup.add_user("dave", "데이브", "abc", ["1" * 18], log=lambda m: None) == 2  # 숫자 아님


def test_ready_requires_access(data, monkeypatch):
    from tests.conftest import CONFIG
    (data / "config.toml").write_text(CONFIG.replace("ready = true", "ready = false"))
    config.reload()
    monkeypatch.setattr(setup, "_commit", lambda *a, **k: None)
    deployed = []
    monkeypatch.setattr(setup.deploy, "worker", lambda u: deployed.append(u.id))
    monkeypatch.setattr(setup, "access_protected", lambda url: (False, "응답 200"))
    assert setup.ready("bob", log=lambda m: None) == 1
    assert not config.user("bob").ready and deployed == []
    monkeypatch.setattr(setup, "access_protected", lambda url: (True, "302 → Access 로그인"))
    assert setup.ready("bob", log=lambda m: None) == 0
    assert config.user("bob").ready and not config.user("alice").ready and deployed == ["bob"]
    assert "ready = true" in (data / "config.toml").read_text().split('id = "bob"')[1]


def test_access_protected(monkeypatch):
    class R:
        def __init__(self, code, loc=""):
            self.status_code, self.headers = code, {"location": loc}

    monkeypatch.setattr(setup.requests, "get", lambda *a, **k: R(302, "https://me.cloudflareaccess.com/cdn-cgi/access/login"))
    assert setup.access_protected("https://x")[0]
    monkeypatch.setattr(setup.requests, "get", lambda *a, **k: R(200))
    assert not setup.access_protected("https://x")[0]


def test_launchd_plist(data):
    p = service.plist("pipeline")
    assert p["Label"] == service.label("pipeline") and p["Label"].endswith(".t2k.pipeline")
    assert p["ProgramArguments"] == [sys.executable, "-m", "t2k", "pipeline"]
    assert p["EnvironmentVariables"]["T2K_HOME"] == str(data) and p["StartInterval"] == 60
    assert service.plist("bot")["KeepAlive"] is True
    plistlib.dumps(p)  # 형식 확인


def test_check_site_runs_in_subprocess(data):
    """agent 가 지시를 적용하기 전에 쓰는 확인: 데이터 폴더를 환경 변수로 받아 사이트를 생성한다."""
    env = {**os.environ, "T2K_HOME": str(data)}
    p = subprocess.run([sys.executable, "-m", "t2k", "check-site", "bob"], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    (data / "users/bob/categories.toml").write_text("깨진 파일")
    p = subprocess.run([sys.executable, "-m", "t2k", "check-site", "bob"], capture_output=True, text=True, env=env)
    assert p.returncode != 0


def test_search_path_skips_app_bundle_wrappers(tmp_path, monkeypatch):
    wrapper = tmp_path / "Term.app/Contents/Resources/bin"
    real = tmp_path / "local/bin"
    for d in (wrapper, real):
        d.mkdir(parents=True)
        (d / "claude").write_text("#!/bin/sh\n")
        (d / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{wrapper}:{real}")
    assert service.find("claude") == str(real / "claude")
    assert str(wrapper) not in service.search_path().split(":")
