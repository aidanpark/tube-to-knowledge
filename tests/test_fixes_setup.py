"""설치 검증(코드 대조)에서 나온 수정: 설정 오류 안내, init 의 언어, 이어 가는 user add, user ready 의 대기열 비우기,
doctor 가 자동 실행과 같은 환경으로 점검하기, 수동 요약의 게시, 안내 페이지 · 토큰 오류 문구.
wrangler · claude · 디스코드 · launchctl · 네트워크는 호출하지 않는다."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from t2k import cli, config, deploy, discord_inbox, doctor, i18n, service, setup

HANGUL = re.compile(r"[가-힣]")


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for k in ("T2K_LANG", "DISCORD_BOT_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def set_lang(data, code: str) -> None:
    f = data / "config.toml"
    f.write_text(re.sub(r'language = "\w+"', f'language = "{code}"', f.read_text()))
    config.reload()


def test_bad_toml_names_the_file(data):
    f = data / "config.toml"
    f.write_text(f.read_text().replace('[site]\n', '[site]\nsubdomain = "x"\n') + '\n[site]\nsubdomain = "y"\n')
    config.reload()
    with pytest.raises(SystemExit) as e:
        config.users()
    assert str(f) in str(e.value) and "[site]" in str(e.value) and "subdomain" in str(e.value)
    assert "읽을 수 없음" in str(e.value)  # 시험 데이터는 language = "ko"
    set_text = f.read_text().replace('language = "ko"', 'language = "en"')
    f.write_text(set_text)
    config.reload()
    with pytest.raises(SystemExit, match="Cannot read"):
        config.users()


@pytest.mark.parametrize("env,code", [(None, "en"), ("ko", "ko")])
def test_init_writes_language_and_template_language(tmp_path, monkeypatch, env, code):
    d = tmp_path / "new"
    monkeypatch.setattr(config, "DATA", d)
    config.reload()
    if env:
        monkeypatch.setenv("T2K_LANG", env)
    monkeypatch.setattr(setup, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    assert setup.init(log=lambda m: None) == 0
    cfg, ign = (d / "config.toml").read_text(), (d / ".gitignore").read_text()
    assert f'language = "{code}"' in cfg and 'subdomain = ""' in cfg
    assert bool(HANGUL.search(cfg + ign)) == (code == "ko")
    monkeypatch.setenv("T2K_LANG", "en" if code == "ko" else "ko")
    config.reload()
    assert i18n.lang() == code  # init 뒤에는 config.toml 의 언어가 T2K_LANG 보다 먼저다


def test_user_add_resumes_after_placeholder_failure(data, monkeypatch):
    made = []
    monkeypatch.setattr(setup, "create_kv", lambda uid: made.append(uid) or "e" * 32)
    monkeypatch.setattr(setup, "_commit", lambda *a, **k: None)

    def fail(u):
        raise RuntimeError("workers.dev subdomain not registered")
    monkeypatch.setattr(setup.deploy, "placeholder", fail)
    lines = []
    assert setup.add_user("carol", "Carol", "123456789012345678", ["223456789012345678"], log=lines.append) == 1
    assert "same t2k user add command" not in "\n".join(lines)  # 시험 데이터는 한국어
    assert "같은 t2k user add 명령을 다시 실행한다" in "\n".join(lines) and config.user("carol").kv_namespace == "e" * 32
    deployed = []
    monkeypatch.setattr(setup.deploy, "placeholder", lambda u: deployed.append(u.id))
    assert setup.add_user("carol", "Carol", "123456789012345678", ["223456789012345678"], log=lambda m: None) == 0
    assert made == ["carol"] and deployed == ["carol"]


def test_user_add_kv_failure_writes_nothing(data, monkeypatch):
    set_lang(data, "en")

    def fail(uid):
        raise RuntimeError("not authenticated")
    monkeypatch.setattr(setup, "create_kv", fail)
    lines = []
    assert setup.add_user("carol", "Carol", "123456789012345678", ["223456789012345678"], log=lines.append) == 1
    out = "\n".join(lines)
    assert "Could not create the edit-queue KV" in out and "wrangler@4.147.0 login" in out
    assert 'id = "carol"' not in (data / "config.toml").read_text()


def test_ready_discards_edits_queued_before_access(data, monkeypatch):
    from tests.conftest import CONFIG
    (data / "config.toml").write_text(CONFIG.replace("ready = true", "ready = false").replace('language = "ko"', 'language = "en"'))
    config.reload()
    monkeypatch.setattr(setup, "_commit", lambda *a, **k: None)
    monkeypatch.setattr(setup, "access_protected", lambda url: (True, "302 → Access login"))
    monkeypatch.setattr(setup.deploy, "worker", lambda u: None)
    deleted = []
    monkeypatch.setattr(setup.site_ops, "pending", lambda: [{"key": "a"}, {"key": "b"}])
    monkeypatch.setattr(setup.site_ops, "delete", deleted.append)
    lines = []
    assert setup.ready("bob", log=lines.append) == 0
    assert deleted == ["a", "b"] and any("Discarded 2 site edit(s)" in l for l in lines)

    def fail():
        raise RuntimeError("wrangler failed")
    monkeypatch.setattr(setup.site_ops, "pending", fail)
    assert setup.ready("alice", log=lines.append) == 1 and not config.user("alice").ready


class FakeDiscord:
    def __init__(self, token):
        pass

    def me(self):
        return {"username": "bot"}

    def call(self, method, path):
        return {"flags": 1 << 18}

    def messages(self, channel_id, limit=1):
        return []


def run_doctor(monkeypatch, runner) -> str:
    monkeypatch.setattr(doctor, "_run", runner)
    monkeypatch.setattr(doctor.shutil, "which", lambda tool, path=None: f"/usr/bin/{tool}")
    monkeypatch.setattr(doctor.sys, "platform", "darwin")
    monkeypatch.setattr(doctor.discord_inbox, "Discord", FakeDiscord)
    monkeypatch.setattr(setup.requests, "get", lambda *a, **k: type("R", (), {
        "status_code": 302, "headers": {"location": "https://t.cloudflareaccess.com/x"}})())
    monkeypatch.setattr(service, "loaded", lambda job: True)
    lines = []
    doctor.run(log=lines.append)
    return "\n".join(lines)


def test_doctor_uses_background_environment(data, monkeypatch):
    set_lang(data, "en")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "from-shell")
    monkeypatch.setattr(discord_inbox, "TOKEN_FILE", data / "missing.env")
    seen = {}

    def runner(args, timeout=120, **kw):
        if args[1:2] == ["-p"]:
            seen["env"] = kw["env"]
            return subprocess.CompletedProcess(args, 1, "", "Not logged in")  # 자동 실행 환경에서는 로그인이 없다
        if args[:2] == ["git", "remote"]:
            return subprocess.CompletedProcess(args, 0, "origin\n", "")
        if "@{u}" in args:
            return subprocess.CompletedProcess(args, 128, "", "no upstream")
        return subprocess.CompletedProcess(args, 0, "logged in", "")
    out = run_doctor(monkeypatch, runner)
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "DISCORD_BOT_TOKEN" not in seen["env"]
    assert seen["env"]["PATH"] == service.search_path() and seen["env"]["T2K_HOME"] == str(config.DATA)
    assert "✗ Claude Code signed in" in out and "ANTHROPIC_API_KEY in your shell is not visible" in out
    assert "The token comes only from DISCORD_BOT_TOKEN" in out and "Token file not found" in out
    assert "0: " not in out
    assert "! The backup remote has no upstream branch yet" in out and f"git -C {config.DATA} push -u origin main" in out


def test_discord_error_without_status_prefix():
    assert str(discord_inbox.DiscordError(0, "Token file not found: x")) == "Token file not found: x"
    assert str(discord_inbox.DiscordError(401, "Unauthorized")) == "401: Unauthorized"


def test_placeholder_follows_language(data, monkeypatch):
    set_lang(data, "en")
    pages = []
    monkeypatch.setattr(deploy, "worker", lambda u, d: pages.append((Path(d) / "index.html").read_text()))
    deploy.placeholder(config.user("alice"))
    assert "Coming soon." in pages[0] and 'lang="en"' in pages[0] and not HANGUL.search(pages[0])


def test_service_lang_fallback_is_not_korean(data, monkeypatch):
    monkeypatch.delenv("LANG", raising=False)
    assert service.plist("pipeline")["EnvironmentVariables"]["LANG"] == "en_US.UTF-8"


def test_manual_summary_publishes_only_open_sites(data, monkeypatch, capsys):
    set_lang(data, "en")
    calls = []
    monkeypatch.setattr("t2k.site.build", lambda **kw: calls.append("build"))
    monkeypatch.setattr("t2k.pipeline.git_publish", lambda msg: calls.append("commit") or True)
    monkeypatch.setattr("t2k.deploy.worker", lambda u: calls.append(f"deploy {u.id}"))
    assert cli.publish(config.user("alice"), "t2k summarize: x") == 0
    assert calls == ["build", "commit", "deploy alice"] and "Published to https://a.example" in capsys.readouterr().out
    calls.clear()
    (data / "config.toml").write_text((data / "config.toml").read_text().replace("ready = true", "ready = false"))
    config.reload()
    assert cli.publish(config.user("alice"), "x") == 0
    assert calls == [] and "t2k user ready alice" in capsys.readouterr().out
    monkeypatch.setattr("t2k.pipeline.git_publish", lambda msg: False)
    (data / "config.toml").write_text((data / "config.toml").read_text().replace("ready = false", "ready = true"))
    config.reload()
    assert cli.publish(config.user("alice"), "x") == 1 and "t2k redeploy alice" in capsys.readouterr().err


def test_discord_check_english(data, monkeypatch):
    set_lang(data, "en")

    class D(FakeDiscord):
        def me(self):
            return {"username": "bot", "id": "9"}

        def messages(self, channel_id, limit=1):
            return [{"id": "1", "author": {"id": "5", "username": "amy"}, "content": "", "embeds": []}]
    monkeypatch.setattr(discord_inbox, "Discord", D)
    monkeypatch.setattr(discord_inbox, "load_token", lambda **kw: "t")
    lines = []
    discord_inbox.check("100", log=lines.append)
    out = "\n".join(lines)
    assert "Bot: bot (9)" in out and "Recent messages: 1" in out and "Message Content Intent may be off" in out
    assert not HANGUL.search(out)
