"""디스코드 지시 처리. launchd 가 1분마다 실행하고, 봇(bot.py)이 지시를 받으면 바로 깨운다 (`ytbook agent`).

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
from .summarize import KST, _claude_bin

WORK = Path.home() / ".local" / "share" / "ytbook" / "agent"  # 지시마다의 worktree (데이터 저장소 밖)
# 작업 공간. 홈 폴더 밖에 둔다: 홈 · 시스템 폴더는 아래 거부 규칙으로 막는다.
# 거부 규칙은 macOS 의 폴더 구조를 전제로 한다. 다른 운영체제에서는 run() 이 실행하지 않는다
WS_ROOT = Path("/private/tmp/ytbook-agent")
TIMEOUT = 1800        # Claude 실행 제한(초)
MAX_REPLY = 1900      # 디스코드 메시지는 2000자까지다
APPROVE = {"승인", "적용"}
CANCEL = {"취소"}
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

PROMPT = """당신은 {name} 님의 영상 요약 사이트를 손보는 도우미다. {name} 님이 디스코드로 아래 요청을 보냈다.

작업 공간:
- mine/ : {name} 님의 파일. 여기만 고칠 수 있다.
  - profile.md : 관심사. 요약의 개인화 항목(시청 권유 · 나에게 주는 의미)에 쓰인다.
  - rules.md : 요약 방식에 대한 요청 (예: 요리 영상은 재료 목록을 정리). 요약할 때 프롬프트에 들어간다.
  - categories.toml : 카테고리 (id · name · scope). scope 는 분류 기준이다. 0099 기타는 지우지 않는다.
    새 id 는 기존 최대값 + 1 (네 자리). 카테고리를 지우면 그 폴더의 요약은 mine/summaries/0099_기타/ 로 옮기고
    파일 앞머리의 category 값도 바꾼다.
  - summaries/ : {name} 님의 요약 (<카테고리>/<날짜>_<동영상ID>.md). 카테고리를 옮길 때만 고친다.
  - templates/ : 사이트 템플릿 덮어쓰기. reference/site_templates/ 의 같은 이름 파일을 복사해 고치면 그 파일이 쓰인다.
    템플릿은 샌드박스에서 렌더링되므로 파이썬 객체의 내부 속성에 접근하는 표현은 쓰지 않는다.
  - style.css : 사이트에 덧붙는 CSS. reference/site_assets/style.css 의 변수 · 클래스를 참고한다.
- reference/ : 참고용 기본 템플릿과 CSS. 고쳐도 적용되지 않는다.

규칙:
- mine/ 밖은 고치지 않는다. 다른 사람의 자료와 이 컴퓨터의 다른 파일은 볼 수도 고칠 수도 없다.
- 요약 파이프라인 · 사이트 생성 코드 · 서버 설정을 바꿔야 하는 요청이면 고치지 말고, 이 기능으로는 할 수 없어
  관리자가 직접 해야 한다고 답한다.
- 외부 스크립트(<script src>)를 넣지 않는다.
- 질문이면 파일을 고치지 말고 답만 한다.
- 마지막 답은 디스코드에 그대로 보낸다: 한국어 존댓말로 바꾼 내용(또는 질문의 답)을 2~6줄로 쓴다.

{history}요청:
{text}"""

HISTORY_HEAD = """이전 대화 (같은 채널의 답장 사슬, 오래된 것부터). 맥락을 이해하는 데에만 쓰고, 처리할 것은 마지막 요청이다:
{lines}

"""


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
        word = ins["text"].strip().rstrip(".!")
        waiting = [t for t in st.tasks if t["user"] == user.id and t["status"] == "awaiting"]
        if word in APPROVE | CANCEL:
            if waiting:  # 기다리는 지시가 없으면 무시한다
                waiting[-1]["decision"] = "approve" if word in APPROVE else "cancel"
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
            who = "도우미"
        elif author in user.owners:
            who = m["author"].get("global_name") or m["author"]["username"]
        else:
            break
        out.append((who, (m.get("content") or "").strip()[:1500]))
        mid = (m.get("message_reference") or {}).get("message_id")
    return out[::-1]


def build_prompt(user: config.User, text: str, past: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"[{who}] {said}" for who, said in past)
    return PROMPT.format(name=user.name, text=text, history=HISTORY_HEAD.format(lines=lines) if past else "")


def run_claude(prompt: str, cwd: Path) -> str:
    """Claude Code 를 비대화형으로 실행하고 마지막 답을 반환한다. 작업 공간 밖과 셸 · 웹은 거부한다."""
    model = config.settings()["agent"]["model"]
    p = subprocess.run([_claude_bin(), "-p", "--model", model, "--output-format", "json", "--no-session-persistence",
                        *ISOLATION, "--allowedTools", *ALLOW, "--disallowedTools", *DENY],
                       input=prompt, capture_output=True, text=True, cwd=cwd, timeout=TIMEOUT)
    if p.returncode != 0:
        raise RuntimeError(f"claude 실패: {(p.stderr or p.stdout).strip()[-500:]}")
    try:
        return (json.loads(p.stdout).get("result") or "").strip()
    except ValueError:
        return p.stdout.strip()


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    # cwd 기본값은 호출할 때의 config.DATA 다 (정의할 때 고정하면 시험이 실제 저장소를 건드린다)
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd or config.DATA, timeout=300)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} 실패: {p.stderr.strip()[-400:]}")
    return p.stdout


def _ytbook(*args: str, data: Path, timeout: int = 600) -> subprocess.CompletedProcess:
    """ytbook 을 data 폴더(YTBOOK_HOME)로 실행한다. 지금 실행 중인 것과 같은 파이썬 · 코드를 쓴다."""
    env = {**os.environ, "YTBOOK_HOME": str(data)}
    return subprocess.run([sys.executable, "-m", "ytbook", *args], capture_output=True, text=True, cwd=data,
                          env=env, timeout=timeout)


def changed_files(worktree: Path) -> list[str]:
    out = _git("status", "--porcelain", "--untracked-files=all", cwd=worktree)
    return sorted({line[3:].split(" -> ")[-1].strip('"') for line in out.splitlines() if line.strip()})


def check(worktree: Path, user: config.User) -> str:
    """그 사람의 사이트가 생성되는지 확인한다 (카테고리 파일 형식 · 템플릿 오류). 실패하면 RuntimeError."""
    b = _ytbook("check-site", user.id, data=worktree)
    if b.returncode != 0:
        raise RuntimeError(f"사이트 생성 실패: {b.stderr.strip()[-400:]}")
    return "사이트 생성 확인"


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
            reply(dc, user.channel_id, task["message_id"], answer or "답을 만들지 못했습니다.")
            _react(dc, user.channel_id, task["message_id"], DONE_MARK, discord_inbox.SEEN)
            cleanup(task)
            return
        if not scope_ok(changed, user):  # copy_back 이 그 사람의 파일만 옮기므로 일어나지 않아야 한다
            raise RuntimeError(f"허용 범위 밖의 파일: {', '.join(changed[:5])}")
        result = check(wt, user)
        _git("add", "-A", cwd=wt)
        _git("commit", "-q", "-m", f"디스코드 지시 ({user.name}): {task['text'][:60]}\n\n{task['text']}\n\n"
                                   "Co-Authored-By: Claude <noreply@anthropic.com>", cwd=wt)
        task["status"], task["files"] = "awaiting", changed
        files = "\n".join(f"- {f}" for f in changed[:15]) + ("\n- …" if len(changed) > 15 else "")
        reply(dc, user.channel_id, task["message_id"],
              f"{answer}\n\n바뀐 파일:\n{files}\n확인: {result}\n\n적용하려면 「승인」, 버리려면 「취소」라고 써 주세요. 이 답장에 답장하면 대화를 이어 갈 수 있고, 새 요청은 「승인」 또는 「취소」 뒤에 처리합니다.")
        _react(dc, user.channel_id, task["message_id"], WAIT, discord_inbox.SEEN)
    except Exception as e:
        task["status"], task["error"] = "failed", str(e)[:500]
        reply(dc, user.channel_id, task["message_id"], f"처리하지 못했습니다: {str(e)[:300]}")
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
                raise RuntimeError(f"합치기 충돌: {(p.stdout + p.stderr).strip()[-300:]}")
            if _git("remote").strip():
                _git("push", "-q")
            task["status"] = "applied"
            ids = [user.id]  # 그 사람의 사이트만
            r = _ytbook("redeploy", *ids, data=config.DATA)
            note = f"사이트에도 반영했습니다: {user.site_url}"
            if r.returncode != 0:  # 파이프라인이 다음 주기에 다시 배포한다 (deploy_pending)
                status = config.path("state") / "status.json"
                d = json.loads(status.read_text()) if status.exists() else {}
                d["deploy_pending"] = sorted(set(d.get("deploy_pending", [])) | set(ids))
                status.write_text(json.dumps(d, ensure_ascii=False, indent=1))
                note = "사이트 배포는 실패해서 파이프라인이 곧 다시 시도합니다."
        reply(dc, user.channel_id, task["message_id"], f"적용했습니다. {note}")
        _react(dc, user.channel_id, task["message_id"], DONE_MARK, WAIT)
    except Exception as e:
        task["status"], task["error"] = "failed", str(e)[:500]
        reply(dc, user.channel_id, task["message_id"], f"적용하지 못했습니다: {str(e)[:300]}")
        _react(dc, user.channel_id, task["message_id"], FAIL_MARK, WAIT)
    finally:
        cleanup(task)


def discard(task: dict, dc) -> None:
    user = next(u for u in config.users() if u.id == task["user"])
    task["status"] = "cancelled"
    cleanup(task)
    reply(dc, user.channel_id, task["message_id"], "취소했습니다. 바꾼 내용은 버렸습니다.")
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
    stale = [t for t in st.tasks if t["status"] == "running"]  # 잠금을 잡았으므로 실행 중인 다른 주기는 없다
    for t in stale:
        t["status"], t["error"] = "failed", "처리 도중 중단됨"
        cleanup(t)
    st.save()
    pending = [t for t in st.tasks if t["status"] == "awaiting" and t.get("decision")] or next_task(st)
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
        for t in pending:
            (apply if t["decision"] == "approve" else discard)(t, dc)
            log(f"{now_iso()} 결정 ({t['user']}) {t['decision']} → {t['status']}")
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
