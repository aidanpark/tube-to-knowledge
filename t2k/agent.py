"""디스코드 지시 처리. launchd 가 1분마다 실행하고, 봇(bot.py)이 지시를 받으면 바로 깨운다 (`t2k agent`).

지시: 사람의 채널에 owners(config.toml)가 쓴 글 중 YouTube 링크가 없는 것 (파이프라인 · 봇이 state/instructions.jsonl 에 남긴다).

범위: 지시는 **그 채널 주인의 사이트 작업**에만 쓴다. 관리자의 지시도 같다.
- 바꿀 수 있는 것은 그 사람의 파일(config.User.owned): 관심사 · 요약 방식 요청 · 템플릿 덮어쓰기 · CSS · 카테고리 · 요약.
  요약 파이프라인 · 사이트 생성 코드 · 설정 · 다른 사람의 자료 · 이 컴퓨터의 시스템은 바꾸지 않는다.
- Claude Code(claude -p)는 그 사람의 파일 사본(mine/)과 참고용 기본 템플릿(reference/)만 있는 작업 공간에서 실행한다.
  작업 공간은 홈 폴더 밖에 두고, 홈 · 시스템 폴더의 읽기 · 쓰기와 셸(Bash) · 웹 도구는 거부 규칙으로 막는다.
- 결과 중 그 사람의 파일만 데이터 저장소의 worktree(브랜치 agent/<id>)에 옮겨 담는다. 범위 밖 변경이 있으면 적용하지 않는다.
- 사이트 생성으로 확인한다. 템플릿은 샌드박스 환경에서 렌더링한다 (site.py).

맥락: 지시가 봇 메시지에 대한 **답장**이면 답장 사슬을 거슬러 올라가 이전 대화를
[agent] history 개까지 함께 넘긴다. 답장이 아니면 새 대화다. 오래 남길 것은 대화가 아니라 그 사람의 파일
(profile.md · rules.md · 카테고리)에 남긴다. 그래서 대화가 길어져도 넘기는 양이 일정하다. 모델은 [agent] model 로 고정한다.

흐름: 처리 → 디스코드에 바뀐 파일과 「승인/취소」 안내(⏳) → 「승인」이면 데이터 저장소에 합치고 (원격이 있으면) 푸시한 뒤 그 사람의 사이트만
다시 배포한다. 「취소」면 버린다. 바뀐 것이 없으면(질문) 답만 한다.
한 사람의 지시는 한 번에 하나씩 처리한다. 승인을 기다리는 지시가 있으면 그 사람의 다음 지시는 기다린다.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from functools import partial
from pathlib import Path

from . import config, discord_inbox
from .i18n import lang, t
from .summarize import KST, _claude_bin

WORK = Path.home() / ".local" / "share" / "t2k" / "agent"  # 지시마다의 worktree (데이터 저장소 밖)
# 작업 공간. 홈 폴더 밖에 둔다: 홈 · 시스템 폴더는 아래 거부 규칙으로 막는다.
# 거부 규칙은 macOS 의 폴더 구조를 전제로 한다. 다른 운영체제에서는 run() 이 실행하지 않는다
WS_ROOT = Path("/private/tmp/t2k-agent")
TIMEOUT = 1800        # Claude 실행 제한(초)
MAX_REPLY = 1900      # 디스코드 메시지는 2000자까지다
# 승인 · 취소로 받는 말 ([general] language 별). 대소문자와 끝의 마침표 · 느낌표는 무시한다
DECISIONS = {"ko": ({"승인", "적용"}, {"취소"}),
             "en": ({"approve", "approved", "apply", "yes"}, {"cancel", "no"})}
WAIT, DONE_MARK, FAIL_MARK = "⏳", "✅", "❌"
ALLOW = ["Read(./**)", "Edit(./**)", "Write(./**)"]  # Glob · Grep 은 Read 규칙을 따른다
# Claude Code 는 읽기 도구(Read · Glob · Grep)를 허용 목록과 관계없이 쓸 수 있어서 거부 규칙으로 막아야 한다
# (2026-10-20 시험: 허용 목록만으로는 작업 공간 밖 파일이 읽혔고, 거부 규칙을 넣자 거부되었다)
_ROOTS = ["~/**", "//Users/**", "//Volumes/**", "//etc/**", "//private/etc/**", "//private/var/**",
          "//private/tmp/claude-*/**", "//Library/**", "//System/**", "//Applications/**", "//opt/**", "//usr/**"]
DENY = ["Bash", "WebFetch", "WebSearch", "NotebookEdit"] + [f"{tool}({r})" for tool in ("Read", "Edit", "Write") for r in _ROOTS]
# 사용자 전역 설정을 읽지 않고(권한 모드 · 허용 규칙 · 훅), MCP 도구를 불러오지 않으며, 허용 목록에 없는 동작은 묻지 않고 거부한다.
# 전역 권한 모드가 auto · bypassPermissions 이면 거부 목록에 없는 쓰기(예: /private/tmp)가 허용되었다 (2026-10-21 시험)
ISOLATION = ["--permission-mode", "dontAsk", "--setting-sources", "project", "--strict-mcp-config"]

log = partial(print, flush=True)

# Claude 에 주는 지시문 · 디스코드 답장 문구는 locales/<언어>/discord.toml 의 discord.agent 에 있다


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


class State:
    """state/agent.json: 지시 목록. status: queued · awaiting(승인 대기) · applied · cancelled · answered · failed."""

    def __init__(self, path: Path):
        self.path = path
        self.d = json.loads(path.read_text()) if path.exists() else {"tasks": [], "seen": []}

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1))
        tmp.replace(self.path)

    @property
    def tasks(self) -> list[dict]:
        return self.d["tasks"]


def ingest(st: State, user: config.User, instructions: list[dict]) -> None:
    """새 지시를 할 일로 넣는다. 「승인」 · 「취소」는 그 사람의 승인 대기 지시에 대한 결정으로 기록한다."""
    seen = set(st.d["seen"])
    for ins in instructions:
        if ins["message_id"] in seen:
            continue
        seen.add(ins["message_id"])
        word = ins["text"].strip().rstrip(".!").lower()
        approve, cancel = DECISIONS[lang()]
        waiting = [t for t in st.tasks if t["user"] == user.id and t["status"] == "awaiting"]
        if word in approve | cancel:
            if waiting:  # 기다리는 지시가 없으면 무시한다
                waiting[-1]["decision"] = "approve" if word in approve else "cancel"
                waiting[-1]["decision_message"] = ins["message_id"]
            continue
        st.tasks.append({"id": ins["message_id"], "user": user.id, "message_id": ins["message_id"],
                         "author_id": ins["author_id"], "text": ins["text"], "reply_to": ins.get("reply_to"),
                         "status": "queued", "created": now_iso()})
    st.d["seen"] = sorted(seen)


def next_task(st: State) -> dict | None:
    busy = {t["user"] for t in st.tasks if t["status"] == "awaiting"}
    return next((t for t in st.tasks if t["status"] == "queued" and t["user"] not in busy), None)


def _rel(path: Path) -> str:
    return path.relative_to(config.DATA).as_posix()


def scope_ok(changed: list[str], user: config.User) -> bool:
    """바뀐 파일이 모두 그 사람의 파일(config.User.owned) 안에 있는가."""
    owned = [_rel(p) for p in user.owned().values()]
    return all(any(c == o or c.startswith(o + "/") for o in owned) for c in changed)


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst)
    elif src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _remove(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def make_workspace(user: config.User, worktree: Path, ws: Path) -> None:
    """작업 공간: mine/<이름> (그 사람의 파일 사본) + reference/ (기본 템플릿 · CSS).
    커밋된 상태(worktree)에서 복사한다. main 작업 트리의 커밋 전 요약이 지시의 변경으로 섞이지 않게 한다."""
    (ws / "mine").mkdir(parents=True)
    for name, path in user.owned().items():
        _copy(worktree / _rel(path), ws / "mine" / name)
    shutil.copytree(config.CODE / "site_templates", ws / "reference" / "site_templates")
    shutil.copytree(config.CODE / "site_assets", ws / "reference" / "site_assets")


def copy_back(ws: Path, worktree: Path, user: config.User) -> None:
    """작업 공간 mine/ 의 그 사람 파일만 worktree 의 같은 자리로 옮긴다. mine/ 의 다른 파일은 버린다."""
    for name, path in user.owned().items():
        dest = worktree / _rel(path)
        _remove(dest)
        _copy(ws / "mine" / name, dest)


def history(dc, user: config.User, reply_to: str | None, limit: int) -> list[tuple[str, str]]:
    """답장 사슬을 거슬러 올라가 (누가, 무엇을) 을 오래된 것부터 반환한다. 그 채널 주인 · 봇의 메시지만 넣는다."""
    out, mid, bot = [], reply_to, None
    while mid and len(out) < limit:
        try:
            m = dc.message(user.channel_id, mid)
        except discord_inbox.DiscordError:
            break  # 지워진 메시지 등: 거기까지만
        bot = bot or dc.me()["id"]
        author = m["author"]["id"]
        if author == bot:
            who = t("discord.agent.helper")
        elif author in user.owners:
            who = m["author"].get("global_name") or m["author"]["username"]
        else:
            break
        out.append((who, (m.get("content") or "").strip()[:1500]))
        mid = (m.get("message_reference") or {}).get("message_id")
    return out[::-1]


def build_prompt(user: config.User, text: str, past: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"[{who}] {said}" for who, said in past)
    return t("discord.agent.prompt", name=user.name, text=text,
             history=t("discord.agent.history_head", lines=lines) if past else "")


def run_claude(prompt: str, cwd: Path) -> str:
    """Claude Code 를 비대화형으로 실행하고 마지막 답을 반환한다. 작업 공간 밖과 셸 · 웹은 거부한다."""
    model = config.settings()["agent"]["model"]
    p = subprocess.run([_claude_bin(), "-p", "--model", model, "--output-format", "json", "--no-session-persistence",
                        *ISOLATION, "--allowedTools", *ALLOW, "--disallowedTools", *DENY],
                       input=prompt, capture_output=True, text=True, cwd=cwd, timeout=TIMEOUT)
    if p.returncode != 0:
        raise RuntimeError(t("discord.agent.claude_error", error=(p.stderr or p.stdout).strip()[-500:]))
    try:
        return (json.loads(p.stdout).get("result") or "").strip()
    except ValueError:
        return p.stdout.strip()


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    # cwd 기본값은 호출할 때의 config.DATA 다 (정의할 때 고정하면 시험이 실제 저장소를 건드린다)
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd or config.DATA, timeout=300)
    if check and p.returncode != 0:
        raise RuntimeError(t("discord.agent.git_error", command=" ".join(args[:2]), error=p.stderr.strip()[-400:]))
    return p.stdout


def _t2k(*args: str, data: Path, timeout: int = 600) -> subprocess.CompletedProcess:
    """t2k 를 data 폴더(T2K_HOME)로 실행한다. 지금 실행 중인 것과 같은 파이썬 · 코드를 쓴다."""
    env = {**os.environ, "T2K_HOME": str(data)}
    return subprocess.run([sys.executable, "-m", "t2k", *args], capture_output=True, text=True, cwd=data,
                          env=env, timeout=timeout)


def changed_files(worktree: Path) -> list[str]:
    out = _git("status", "--porcelain", "--untracked-files=all", cwd=worktree)
    return sorted({line[3:].split(" -> ")[-1].strip('"') for line in out.splitlines() if line.strip()})


def check(worktree: Path, user: config.User) -> str:
    """그 사람의 사이트가 생성되는지 확인한다 (카테고리 파일 형식 · 템플릿 오류). 실패하면 RuntimeError."""
    b = _t2k("check-site", user.id, data=worktree)
    if b.returncode != 0:
        raise RuntimeError(t("discord.agent.site_check_failed", error=b.stderr.strip()[-400:]))
    return t("discord.agent.site_check_ok")


def reply(dc, channel_id: str, message_id: str, text: str) -> None:
    dc.reply(channel_id, message_id, text[:MAX_REPLY])


def _react(dc, channel_id: str, message_id: str, emoji: str, remove: str | None = None) -> None:
    try:
        if remove:
            dc.unreact(channel_id, message_id, remove)
        dc.react(channel_id, message_id, emoji)
    except discord_inbox.DiscordError:
        pass  # 표시일 뿐이다


def worktree_of(task: dict) -> Path:
    return WORK / f"wt-{task['id']}"


def cleanup(task: dict) -> None:
    wt = worktree_of(task)
    if wt.exists():
        _git("worktree", "remove", "--force", str(wt), check=False)
    _git("branch", "-D", f"agent/{task['id']}", check=False)
    shutil.rmtree(WS_ROOT / f"ws-{task['id']}", ignore_errors=True)


def run_task(task: dict, dc, runner=run_claude) -> None:
    user = next(u for u in config.users() if u.id == task["user"])
    _react(dc, user.channel_id, task["message_id"], discord_inbox.SEEN)
    WORK.mkdir(parents=True, exist_ok=True)
    wt = worktree_of(task)
    cleanup(task)
    _git("worktree", "add", "-q", str(wt), "-b", f"agent/{task['id']}", "HEAD")
    try:
        ws = WS_ROOT / f"ws-{task['id']}"
        make_workspace(user, wt, ws)
        past = history(dc, user, task.get("reply_to"), config.settings()["agent"]["history"])
        answer = runner(build_prompt(user, task["text"], past), ws)
        copy_back(ws, wt, user)
        changed = changed_files(wt)
        if not changed:
            task["status"] = "answered"
            reply(dc, user.channel_id, task["message_id"], answer or t("discord.agent.no_answer"))
            _react(dc, user.channel_id, task["message_id"], DONE_MARK, discord_inbox.SEEN)
            cleanup(task)
            return
        if not scope_ok(changed, user):  # copy_back 이 그 사람의 파일만 옮기므로 일어나지 않아야 한다
            raise RuntimeError(t("discord.agent.out_of_scope", files=", ".join(changed[:5])))
        result = check(wt, user)
        _git("add", "-A", cwd=wt)
        _git("commit", "-q", "-m", f"디스코드 지시 ({user.name}): {task['text'][:60]}\n\n{task['text']}\n\n"
                                   "Co-Authored-By: Claude <noreply@anthropic.com>", cwd=wt)
        task["status"], task["files"] = "awaiting", changed
        files = "\n".join(f"- {f}" for f in changed[:15]) + ("\n- …" if len(changed) > 15 else "")
        reply(dc, user.channel_id, task["message_id"],
              t("discord.agent.awaiting", answer=answer, files=files, result=result))
        _react(dc, user.channel_id, task["message_id"], WAIT, discord_inbox.SEEN)
    except Exception as e:
        task["status"], task["error"] = "failed", str(e)[:500]
        reply(dc, user.channel_id, task["message_id"], t("discord.agent.failed", error=str(e)[:300]))
        _react(dc, user.channel_id, task["message_id"], FAIL_MARK, discord_inbox.SEEN)
        cleanup(task)


def apply(task: dict, dc) -> None:
    """승인된 지시를 main 에 합치고 푸시한 뒤 사이트를 다시 배포한다. 파이프라인의 커밋과 겹치지 않게 잠금을 잡는다."""
    user = next(u for u in config.users() if u.id == task["user"])
    lock_path = config.path("state") / "pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            p = subprocess.run(["git", "merge", "--no-ff", "-q", f"agent/{task['id']}", "-m",
                                f"합치기: 디스코드 지시 ({user.name})\n\nCo-Authored-By: Claude <noreply@anthropic.com>"],
                               capture_output=True, text=True, cwd=config.DATA, timeout=300)
            if p.returncode != 0:
                _git("merge", "--abort", check=False)
                raise RuntimeError(t("discord.agent.merge_conflict", error=(p.stdout + p.stderr).strip()[-300:]))
            if _git("remote").strip():
                _git("push", "-q")
            task["status"] = "applied"
            ids = [user.id]  # 그 사람의 사이트만
            r = _t2k("redeploy", *ids, data=config.DATA)
            note = t("discord.agent.site_updated", url=user.site_url)
            if r.returncode != 0:  # 파이프라인이 다음 주기에 다시 배포한다 (deploy_pending)
                status = config.path("state") / "status.json"
                d = json.loads(status.read_text()) if status.exists() else {}
                d["deploy_pending"] = sorted(set(d.get("deploy_pending", [])) | set(ids))
                status.write_text(json.dumps(d, ensure_ascii=False, indent=1))
                note = t("discord.agent.deploy_retry")
        reply(dc, user.channel_id, task["message_id"], t("discord.agent.applied", note=note))
        _react(dc, user.channel_id, task["message_id"], DONE_MARK, WAIT)
    except Exception as e:
        task["status"], task["error"] = "failed", str(e)[:500]
        reply(dc, user.channel_id, task["message_id"], t("discord.agent.apply_failed", error=str(e)[:300]))
        _react(dc, user.channel_id, task["message_id"], FAIL_MARK, WAIT)
    finally:
        cleanup(task)


def discard(task: dict, dc) -> None:
    user = next(u for u in config.users() if u.id == task["user"])
    task["status"] = "cancelled"
    cleanup(task)
    reply(dc, user.channel_id, task["message_id"], t("discord.agent.cancelled"))
    _react(dc, user.channel_id, task["message_id"], FAIL_MARK, WAIT)


MAX_ROUNDS = 10  # 한 번 깨어났을 때 이어서 처리할 최대 횟수 (그 사이 새로 온 지시 · 승인도 처리한다)


def cycle(dc=None, runner=run_claude) -> None:
    for _ in range(MAX_ROUNDS):
        if not step(dc, runner):
            return


def step(dc=None, runner=run_claude) -> bool:
    """지시 목록을 읽고 할 일 하나(지시 하나 또는 승인 · 취소 결정들)를 처리한다. 한 일이 없으면 False."""
    common = config.path("state")
    st = State(common / "agent.json")
    for u in config.users():
        with config.as_user(u):
            ingest(st, u, discord_inbox.Inbox(config.path("state")).instructions())
    stale = [x for x in st.tasks if x["status"] == "running"]  # 잠금을 잡았으므로 실행 중인 다른 주기는 없다
    for x in stale:
        x["status"], x["error"] = "failed", "처리 도중 중단됨"
        cleanup(x)
    st.save()
    pending = [x for x in st.tasks if x["status"] == "awaiting" and x.get("decision")] or next_task(st)
    if not pending:
        return False
    dc = dc or discord_inbox.Discord(discord_inbox.load_token())
    if isinstance(pending, dict):
        log(f"{now_iso()} 지시 처리 ({pending['user']}): {pending['text'][:80]}")
        pending["status"] = "running"
        st.save()
        run_task(pending, dc, runner)
        log(f"{now_iso()} → {pending['status']}")
    else:
        for x in pending:  # t 는 번역 함수이므로 쓰지 않는다
            (apply if x["decision"] == "approve" else discard)(x, dc)
            log(f"{now_iso()} 결정 ({x['user']}) {x['decision']} → {x['status']}")
    st.save()
    return True


def run() -> int:
    """잠금을 잡고 한 주기를 실행한다. 이미 실행 중이면 바로 끝낸다 (Claude 실행은 몇 분 걸린다)."""
    if sys.platform != "darwin":
        log("agent 는 macOS 에서만 실행한다: 작업 공간 밖을 막는 거부 규칙이 macOS 의 폴더 구조를 전제로 한다")
        return 1
    common = config.path("state")
    common.mkdir(parents=True, exist_ok=True)
    with open(common / "agent.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        cycle()
    return 0
