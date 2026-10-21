"""명령줄 · doctor 의 언어: 설정 파일이 없을 때의 언어, T2K_LANG, doctor · 사용법 오류 · init · 자동 실행 안내 문구.
claude · 디스코드 · wrangler · launchctl · 네트워크는 호출하지 않는다."""
import json
import re
import subprocess
import sys

import pytest

from t2k import cli, config, doctor, i18n, service, setup

HANGUL = re.compile(r"[가-힣]")


@pytest.fixture(autouse=True)
def no_env_lang(monkeypatch):
    monkeypatch.delenv("T2K_LANG", raising=False)


def set_lang(data, code: str) -> None:
    f = data / "config.toml"
    f.write_text(re.sub(r'language = "\w+"', f'language = "{code}"', f.read_text()))
    config.reload()


def no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "none")
    config.reload()


def test_lang_fallback_and_env(data, tmp_path, monkeypatch):
    assert i18n.lang() == "ko"  # 시험 데이터 폴더의 설정
    monkeypatch.setenv("T2K_LANG", "en")
    assert i18n.lang() == "ko"  # config.toml 이 있으면 T2K_LANG 을 보지 않는다 (수동 명령과 자동 실행의 언어가 같다)
    monkeypatch.delenv("T2K_LANG")
    no_config(tmp_path, monkeypatch)
    assert i18n.lang() == "en"  # config.toml 이 없으면 영어 (끝내지 않는다)
    monkeypatch.setenv("T2K_LANG", "ko")
    assert i18n.lang() == "ko"
    monkeypatch.setenv("T2K_LANG", "fr")
    with pytest.raises(SystemExit):
        i18n.lang()


def test_config_errors_follow_language(tmp_path, monkeypatch):
    no_config(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="Config file not found"):
        config.users()
    monkeypatch.setenv("T2K_LANG", "ko")
    config.reload()
    with pytest.raises(SystemExit, match="설정 파일이 없음"):
        config.users()


class FakeDiscord:
    def __init__(self, token):
        pass

    def me(self):
        return {"username": "bot"}

    def call(self, method, path):
        return {"flags": 1 << 18}

    def messages(self, channel_id, limit=1):
        return []


def fake_run(args, timeout=120, **kw):
    out = ""
    if args[1:2] == ["-p"]:
        out = json.dumps({"is_error": False, "result": "OK"})
    elif args[:2] == ["git", "remote"]:
        out = "origin\n"
    elif "whoami" in args:
        out = "logged in"
    return subprocess.CompletedProcess(args, 0, out, "")


class Resp:
    status_code = 302
    headers = {"location": "https://team.cloudflareaccess.com/cdn-cgi/access/login"}


def run_doctor(monkeypatch) -> list[str]:
    monkeypatch.setattr(doctor, "_run", fake_run)
    monkeypatch.setattr(doctor.shutil, "which", lambda tool, path=None: f"/usr/bin/{tool}")
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(doctor.discord_inbox, "load_token", lambda **kw: "token")
    monkeypatch.setattr(doctor.discord_inbox, "Discord", FakeDiscord)
    monkeypatch.setattr(setup.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(service, "loaded", lambda job: True)
    lines = []
    assert doctor.run(log=lines.append) == 0
    return lines


def test_doctor_english(data, monkeypatch):
    set_lang(data, "en")
    out = "\n".join(run_doctor(monkeypatch))
    for s in ("[Tools]", "✓ Claude Code signed in (", "[Data folder ", "✓ git repository (at least one commit)",
              "✓ People (2): alice, bob", "[Discord]", "✓ Bot: bot", "✓ alice: can read channel 100",
              "✓ wrangler signed in", "302 → Access login", "[Background jobs (launchd)]", "No problems found"):
        assert s in out, s
    assert not HANGUL.search(out)


def test_doctor_korean_unchanged(data, monkeypatch):
    out = "\n".join(run_doctor(monkeypatch))
    for s in ("[프로그램]", "✓ Claude Code 로그인 (", "✓ git 저장소 (커밋 1개 이상)", "✓ 사람 2명: alice, bob",
              "✓ alice: 채널 100 읽기", "302 → Access 로그인", "[자동 실행 (launchd)]", "문제 없음"):
        assert s in out, s


def test_doctor_failures_english(data, monkeypatch):
    set_lang(data, "en")
    monkeypatch.setattr(doctor, "_run", fake_run)
    monkeypatch.setattr(doctor.shutil, "which", lambda tool, path=None: None)
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    real = doctor.discord_inbox.load_token
    monkeypatch.setattr(doctor.discord_inbox, "load_token", lambda **kw: real(data / "missing.env", **kw))
    monkeypatch.setattr(setup.requests, "get", lambda *a, **k: type("R", (), {"status_code": 200, "headers": {}})())
    monkeypatch.setattr(service, "loaded", lambda job: False)
    lines = []
    assert doctor.run(log=lines.append) == 1
    out = "\n".join(lines)
    for s in ("✗ yt-dlp: not found", "→ brew install yt-dlp", "✗ Bot token: ", "Token file not found",
              "→ Save the token to ", "The site opens without Access!", "→ t2k service install", "item(s) to fix"):
        assert s in out, s
    assert not HANGUL.search(out)


def test_cli_usage_and_help(tmp_path, monkeypatch, capsys):
    no_config(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["t2k", "user", "add"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2
    assert "--name NAME --channel CHANNEL_ID --owner USER_ID" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["t2k", "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out
    assert "The data folder is the T2K_HOME" in out and "--owner OWNER" in out and not HANGUL.search(out)
    monkeypatch.setenv("T2K_LANG", "ko")
    monkeypatch.setattr(sys, "argv", ["t2k", "user", "add"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "--name 이름 --channel 채널ID" in capsys.readouterr().err


@pytest.mark.parametrize("code,created,nxt", [("en", "Created: ", "Next: in config.toml, write your workers.dev subdomain"), ("ko", "만듦: ", "다음: config.toml 의")])
def test_init_messages(tmp_path, monkeypatch, code, created, nxt):
    no_config(tmp_path, monkeypatch)
    if code == "ko":
        monkeypatch.setenv("T2K_LANG", "ko")
    monkeypatch.setattr(setup, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    lines = []
    assert setup.init(log=lines.append) == 0
    out = "\n".join(lines)
    assert created in out and nxt in out
    if code == "en":
        assert not HANGUL.search(out)


def test_service_messages(data, tmp_path, monkeypatch):
    set_lang(data, "en")
    monkeypatch.setattr(service, "AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(service, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(service.sys, "platform", "darwin")
    monkeypatch.setattr(service, "_launchctl", lambda *a: subprocess.CompletedProcess(a, 1 if a[0] == "print" else 0, "", ""))
    lines = []
    assert service.install(log=lines.append) == 0
    assert any(l.startswith("Registered ") and ".t2k.pipeline → " in l for l in lines)
    assert lines[-1] == f"Logs: {tmp_path / 'logs'}/<job>.log"
    lines.clear()
    service.status(log=lines.append)
    assert all(l.endswith(": not registered") for l in lines) and len(lines) == 3
    lines.clear()
    service.uninstall(log=lines.append)
    assert all(l.startswith("Removed ") for l in lines)
