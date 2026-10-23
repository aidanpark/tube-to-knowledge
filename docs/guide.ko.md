# tube-to-knowledge 안내서

[← README](../README.ko.md) · [English](guide.md) | 한국어

직접 설치하는 방법, 사용법, 문제 해결, 보안, 구성을 설명한다. AI 코딩 에이전트에게 설치를 맡기려면 [AGENT_INSTALL.md](../AGENT_INSTALL.md)(영문)를 본다.

## 시작하기 전에

| 조건 | 이유 |
|---|---|
| **늘 켜 두는 맥** (맥 미니 등, macOS) | 1분마다 디스코드를 확인하고 요약한다. 자동 실행(launchd)과 디스코드 지시 기능은 macOS 에서만 지원한다 |
| **집 인터넷** | YouTube 는 클라우드 서버의 자막 요청을 차단하는 경우가 많다. 집 회선의 맥에서 실행하는 것을 전제로 한다 |
| **Claude 구독 또는 API** | 요약(`claude -p`, 기본 sonnet)과 지시 처리(기본 opus)에 Claude Code 를 쓴다. 요금은 각자 부담한다 |
| **디스코드 계정 · 서버** | 입력 창구다. 무료 |
| **Cloudflare 계정** | 요약 사이트(Workers)와 접근 제어(Access)에 쓴다. 개인 사용량은 무료 한도 안에 든다 |
| **터미널 사용 경험** | 명령을 붙여 넣어 실행할 수 있으면 충분하다 |

> **요소 교체:** 위 표는 t2k 가 지원하는 구성이다. t2k 의 핵심은 영상을 수집하여 요약으로 정리하는 파이프라인이며, 그 주변의 컴퓨터 · AI · 메신저 · 호스팅은 교체할 수 있다. 다만 교체는 사용자가 직접 하는 커스텀이고, 이 안내서는 다루지 않는다. 포팅할 부분은 `summarize.py` · `weekly.py` · `agent.py` · `doctor.py` 의 `claude -p` 호출(AI), `discord_inbox.py` · `bot.py`(메신저), `service.py`(자동 실행), `deploy.py` · `site_ops.py` · `t2k/worker/`(호스팅과 접근 제어)이다. 무엇을 만들든 [AGENT_INSTALL.md](../AGENT_INSTALL.md#rules)의 안전 규칙은 그대로 지킨다.

처음 설치하는 데 약 1시간이 걸린다. 각 단계 끝의 **확인**이 통과하면 다음 단계로 넘어간다. 언제든 `t2k doctor` 를 실행하면 빠진 단계를 표시한다.

> **이용 조건:** 이 프로그램은 yt-dlp 로 YouTube 자막을 받는다. 이 방식이 YouTube 이용 약관과 충돌할 수 있으므로, 본인의 책임으로 개인적인 용도로만 사용한다. 요약에는 남의 영상 내용이 담기므로 사이트는 반드시 Access 로 비공개로 둔다 (5단계). 이 프로그램은 Access 를 확인하기 전에는 요약을 올리지 않는다.

## 설치

### 1. 프로그램 설치

[Homebrew](https://brew.sh) 를 설치한 뒤 다음을 실행한다.

```sh
brew install uv yt-dlp deno node git
```

| 프로그램 | 용도 |
|---|---|
| uv | 파이썬 3.12 환경(uv 가 설치한다. `.python-version` 으로 고정)과 실행 |
| yt-dlp | 자막 · 동영상 정보. 받기가 계속 실패하면 t2k 가 새 판을 확인해 `brew upgrade yt-dlp` 를 실행한다 |
| deno | yt-dlp 가 YouTube 를 처리할 때 쓰는 JavaScript 실행기 |
| node (npx) | Cloudflare 배포 도구 wrangler 실행 (`npx wrangler@4.147.0`, 판을 고정한다) |

**Claude Code** 를 설치하고(https://claude.com/claude-code), 터미널에서 `claude` 를 한 번 실행해 로그인한다(`/login`). 셸의 API 키로 쓰지 말고 이 방법으로 로그인한다: 자동 실행 작업은 셸의 환경 변수를 보지 못하므로, `ANTHROPIC_API_KEY` 를 export 해 두면 터미널에서는 되지만 t2k 에서는 안 된다. `t2k doctor` 는 자동 실행 작업과 같은 환경으로 Claude 를 점검한다.

### 2. t2k 받기와 데이터 폴더

t2k 는 **코드**(이 저장소)와 **데이터**(요약 · 설정, 기본값 `~/t2k-data`)를 다른 폴더에 둔다. 데이터 폴더는 git 저장소로 만들어 변경 기록을 남긴다.

```sh
git clone https://github.com/aidanpark/tube-to-knowledge.git ~/tube-to-knowledge
cd ~/tube-to-knowledge
uv sync                           # 의존성 설치 (.venv)
uv run pytest -q                  # 시험. 외부 서비스는 호출하지 않는다
echo 'alias t2k="$HOME/tube-to-knowledge/.venv/bin/t2k"' >> ~/.zshrc && source ~/.zshrc
t2k init                       # ~/t2k-data 를 만든다
```

- **언어:** 한국어로 쓰려면 `T2K_LANG=ko t2k init` 으로 데이터 폴더를 만든다. 그러면 `config.toml` 에 `[general] language = "ko"` 가 쓰이고 주석도 한국어로 만들어진다. 그냥 `t2k init` 이면 영어(`"en"`)이며, 나중에 그 줄을 `"ko"` 로 바꿔도 된다. 사람을 추가하기 전에 정한다: 사람의 첫 카테고리와 관심사 파일이 이 언어로 만들어진다. 사이트 · 요약 · 주간 요약 · 디스코드 답장 · 명령줄(`t2k doctor` 포함)이 이 설정을 따르고, `config.toml` 이 생긴 뒤에는 `T2K_LANG` 을 보지 않는다. 자동 실행 기록(`~/Library/Logs/t2k/`)과 `t2k sync-ops` · `t2k weekly` 의 진행 표시는 아직 한국어다.
- 데이터 폴더를 다른 곳에 두려면 `~/.zshrc` 에 `export T2K_HOME=폴더` 를 추가한 뒤 `t2k init` 을 실행한다.
- 데이터 폴더를 백업하려면 **비공개** 원격 저장소를 만들어 `git -C ~/t2k-data remote add origin <주소>` 로 연결하고, 터미널에서 `git -C ~/t2k-data push -u origin main` 을 한 번 실행한다 (git 자격 증명도 이때 저장된다). 그 뒤로는 t2k 가 변경할 때마다 푸시한다.
- `t2k init` 이 「커밋하지 못함」이라고 하면 git 사용자 정보를 설정한 뒤 다시 실행한다: `git config --global user.name "이름"`, `git config --global user.email "메일"`.

**확인:** `t2k doctor` 의 [프로그램] · [데이터 폴더]가, 아직 하지 않은 subdomain 과 사람 항목을 빼고 ✓ 이다.

### 3. 디스코드 봇

1. https://discord.com/developers/applications → **New Application** → 이름(예: t2k) 를 정한다.
2. 왼쪽 **Bot** 화면에서
   - **Privileged Gateway Intents → Message Content Intent 를 켜고 저장한다.** 꺼져 있으면 링크와 글의 내용이 빈 값으로 온다.
   - **Reset Token** → 새 토큰의 **Copy** 를 누른다. 토큰은 이때 한 번만 보인다.
3. 토큰을 파일에 저장한다. 아래 명령은 클립보드의 토큰을 저장하므로, **명령을 복사해 붙이지 말고 직접 입력한다** (명령을 복사하면 클립보드의 토큰이 덮어써진다).
   ```sh
   mkdir -p ~/.config/t2k
   (umask 077; printf 'DISCORD_BOT_TOKEN=%s\n' "$(pbpaste)" > ~/.config/t2k/discord.env); pbcopy < /dev/null
   ```
   토큰은 비밀번호와 같다. 채팅 · 저장소에 남기지 않는다.
4. 봇을 서버에 초대한다. 서버가 없으면 디스코드 앱에서 먼저 만든다 (왼쪽 **+** → 직접 만들기).
   개발자 포털 → **OAuth2 → URL Generator** → Scopes **bot** → Bot Permissions **View Channels · Send Messages · Read Message History · Add Reactions · Embed Links** → 아래에 생성된 주소를 브라우저에서 열고 서버를 선택한다.

**확인:** `t2k doctor` 의 [디스코드]에서 봇 이름과 Message Content Intent 가 ✓ 이다.

### 4. 디스코드 채널과 ID

1. 디스코드 설정 → 고급 → **개발자 모드** 를 켠다. 이제 채널 · 사람을 우클릭하면 **ID 복사** 가 나온다.
2. 가족을 서버에 초대한다: 서버 이름 → 초대하기. 디스코드의 나이 제한(한국은 만 14세)을 확인한다.
3. 사람마다 **비공개 채널**을 하나 만든다 (예: `유튜브정리-앨리스`). 채널 만들기 → 비공개 채널 켜기 → 그 사람과 봇을 추가하고, 채널 설정 → 권한 → 그 사람에게 **메시지 기록 보기 · 링크 첨부(Embed Links) · 반응 추가**를 허용한다 (허용하지 않으면 봇의 답장을 다시 볼 수 없다).
   두 번째 채널부터는 첫 채널을 우클릭 → **채널 복제** 로 만들면 비공개 설정과 봇 권한이 따라온다.
4. 각 채널의 **채널 ID**, 그리고 그 채널에서 지시할 사람(본인, 관리자)의 **사용자 ID**를 복사해 둔다.

### 5. Cloudflare 와 사람 추가

#### 5-1. 로그인과 하위 도메인

```sh
npx --yes wrangler@4.147.0 login     # 브라우저에서 Cloudflare 로그인 · 허용
```

Cloudflare 대시보드 → **Workers & Pages** 를 열면 계정의 workers.dev 하위 도메인이 보인다 (처음이면 정하라고 한다). 데이터 폴더의 `config.toml` 에는 `t2k init` 이 만든 `[site]` 표와 빈 `subdomain = ""` 줄이 이미 있다. 그 줄에 값을 쓴다. `[site]` 표를 새로 추가하지 않는다:

```toml
subdomain = "myname"     # 사이트 주소가 https://<id>-yt.myname.workers.dev 가 된다
```

> **주의:** wrangler 명령을 데이터 폴더나 `site/` 가 있는 폴더에서 직접 실행하지 않는다. 일부 wrangler 명령은 실행한 폴더의 `site/` 를 보호 없이 공개 배포한다. t2k 는 항상 빈 임시 폴더에서 wrangler 를 실행한다.

#### 5-2. 사람 추가

```sh
t2k user add --id alice --name 앨리스 --channel <채널 ID> --owner <앨리스 사용자 ID> --owner <관리자 사용자 ID>
```

- `--id` 는 영문 소문자 · 숫자다. 폴더 이름과 사이트 주소에 쓴다.
- `--owner` 는 이 채널에서 **지시**를 받을 사람이다. 링크는 채널의 누구나 올릴 수 있지만, 지시는 owner 만 할 수 있다.
- 이 명령은 사람 폴더(`users/alice/`: 카테고리 「기타」 하나, 관심사 `profile.md`)와 사이트 조작 대기열(KV)을 만들고, **「준비 중」 한 줄만 있는 사이트**를 올린다. 요약은 아직 올리지 않는다.
- 도중에 멈추면(wrangler 로그인이 풀렸거나 안내 페이지 배포가 실패한 경우 등) 문제를 고친 뒤 같은 명령을 다시 실행한다. 이미 기록한 것에서 이어 간다.

#### 5-3. Access 로 사이트 잠그기

1. Cloudflare 대시보드 → **Zero Trust**. 처음이면 팀 이름을 정하고 **Free** 요금제를 선택한다 (계정에 따라 결제 수단 등록을 요구할 수 있다).
2. Zero Trust → Access controls → **Policies → Add a policy**: 이름(예: 「앨리스와 관리자」), Action **Allow**, Include **Emails** 에 이 사이트를 열 사람의 이메일을 넣는다.
3. 대시보드 → Workers & Pages → `alice-yt` → **Protect this Worker behind Access** (Settings → Domains & Routes 의 workers.dev 항목에도 있다) → Scope **All traffic** → 2의 정책 선택 → 적용.

화면 이름은 Cloudflare 가 바꾸는 일이 있다 (2026-10 기준). 찾기 어려우면 대시보드 검색창에 「Access」를 입력한다.

#### 5-4. 사이트 열기

```sh
t2k user ready alice
```

사이트 주소가 Access 로그인 화면으로 넘어가는지(302) 확인한 뒤에만 `ready = true` 로 바꾸고 요약 사이트를 올린다. 확인이 실패하면 아무것도 올리지 않는다.

사람마다 5-2 ~ 5-4 를 반복한다. `t2k user list` 로 목록을 본다.

**확인:** `t2k doctor` 의 [Cloudflare]에서 각 사람의 사이트가 「302 → Access 로그인」이다. **「사이트가 Access 없이 열린다」가 나오면 즉시 Access 설정을 확인한다.**

### 6. 자동 실행

```sh
t2k service install
```

작업 세 개를 macOS launchd 에 등록한다. 맥을 다시 켜고 로그인하면 자동으로 실행된다.

| 작업 | 하는 일 | 주기 | 기록 (`~/Library/Logs/t2k/`) |
|---|---|---|---|
| `com.<사용자>.t2k.pipeline` | 링크를 읽어 요약하고 사이트를 올린다. 일요일 자정 뒤에는 주간 요약을 만든다 | 1분 | `pipeline.log` |
| `com.<사용자>.t2k.agent` | 디스코드 지시를 처리한다 | 1분 + 봇이 호출 | `agent.log` |
| `com.<사용자>.t2k.bot` | 디스코드에 상시 접속해 지시를 바로 받는다 | 상시 | `bot.log` |

상태는 `t2k service status`, 해제는 `t2k service uninstall` 이다.

**확인:** `t2k doctor` 가 「문제 없음」이다. 휴대폰 YouTube 앱에서 영상 → 공유 → 디스코드 → 자기 채널을 선택해 보낸다. 1분 안에 👀, 몇 분 뒤 ✅ 와 요약 주소가 답장으로 온다.

## 사용

- **요약 요청:** YouTube 링크를 자기 채널에 공유한다. 공유 화면에 채널이 안 보이면 공유 화면의 검색창에 채널 이름을 입력한다.
- **지시:** 링크 없이 글을 쓴다 ([말로 고치는 사이트](../README.ko.md#말로-고치는-사이트)).
- **사이트 조작:** 요약 화면 아래에서 카테고리를 옮기거나 요약을 삭제하고, 카테고리 화면에서 카테고리를 추가한다. 몇 분 안에 반영된다.
- **수동 명령:** `t2k summarize <URL> --user alice`(바로 요약하고, 열린 사이트면 바로 게시) · `t2k weekly 2026-W42`(그 주의 주간 요약을 만들고 배포. 비우면 지난주) · `t2k redeploy`(사이트 다시 올리기) · `t2k sync-ops`(사이트 조작 바로 반영) · `t2k discord-check`.

### 사람별 파일 (`<데이터 폴더>/users/<id>/`)

| 파일 | 내용 |
|---|---|
| `profile.md` | 관심사. 요약의 개인화 항목에 쓰인다 |
| `rules.md` | 요약 방식 요청 (예: 요리 영상은 재료 목록을 정리) |
| `categories.toml` | 카테고리 (이름 · 설명). 설명이 Claude 의 분류 기준이 된다 |
| `templates/` · `style.css` | 사이트 모양. 기본 템플릿(`t2k/site_templates/`)의 같은 이름 파일을 덮어쓰고, CSS 를 덧붙인다 |
| `summaries/` | 요약. 커밋한다 |
| `weekly/` | 주간 요약 (`<YYYY-Www>/<카테고리 id>.json`). 커밋한다 |
| `site/` · `state/` | 생성한 사이트 · 처리 상태. 커밋하지 않는다 |

설정의 기본값은 `t2k/defaults.toml` 에 있다. 바꾸려면 데이터 폴더의 `config.toml` 에 같은 표와 키를 쓴다 (예: `[summary]` 아래 `model = "haiku"`).

## 업데이트

```sh
cd ~/tube-to-knowledge && git pull && uv sync && t2k service install && t2k doctor
```

## 문제 해결

| 증상 | 확인 · 조치 |
|---|---|
| 👀 가 안 달린다 | `t2k doctor` 의 [디스코드]. `bot.log` 에 「접속」이 있는지 본다. 토큰을 다시 발급했으면 `discord.env` 를 바꾸고 `t2k service install` |
| 링크를 올렸는데 반응이 없고 로그에 내용이 비어 있다 | Message Content Intent 가 꺼져 있다 (3단계) |
| 로그에 「차단으로 쉬는 중」 | YouTube 가 집 IP 의 자막 요청을 막았다 (429). 30분 → 1시간 → 2시간 → 4시간 간격으로 다시 시도한다. 4시간 이상이면 사이트 홈에 안내가 나온다 |
| 「claude -p 오류」 | Claude Code 로그인이 풀렸거나 사용 한도에 도달했다. 터미널에서 `claude` 를 실행해 확인한다. 「claude not found in PATH」이면 `t2k service install` 을 다시 실행한다 |
| 「조작 대기열 확인 실패 … [code: 10000]」 | wrangler 로그인 갱신 직후의 일시 오류. 자동으로 다시 시도한다. 계속되면 `npx --yes wrangler@4.147.0 login` |
| 사이트 배포 실패 | `pipeline.log` 의 「사이트 배포 실패」. 다음 주기에 다시 시도한다. 수동: `t2k redeploy <id>` |
| 가족이 봇의 답장을 다시 볼 수 없다 | 그 사람에게 채널의 메시지 기록 보기 권한이 없다 (4단계) |
| 지시에 반응이 없다 | `agent.log`. 그 사람의 지시가 승인 대기(⏳)이면 「승인」 · 「취소」 뒤에 다음 지시를 처리한다 |
| 채널 · owner ID 를 잘못 넣었다 | `config.toml` 에서 그 사람의 `[[user]]` 항목(`channel_id`, `owners`)을 고친 뒤 `t2k service install` 을 실행한다 (상시 접속 봇이 다시 읽는다). 채널을 읽을 수 있는지는 `t2k doctor` 로 확인한다 |
| 모든 명령이 「…config.toml 를 읽을 수 없음」으로 멈춘다 | 파일에 문법 오류가 있다. 흔히 `[site]` 표를 두 번 쓴 경우다. 메시지가 가리키는 줄을 고친다 |
| `t2k user add` 가 도중에 멈췄다 | 메시지의 문제를 고친 뒤 같은 명령을 다시 실행한다. 이어서 진행한다 |

## 보안

- **사이트:** Cloudflare Access 로 지정한 이메일만 연다. 새 사이트는 내용 없는 안내 페이지로 만들고, Access 로그인으로 넘어가는 것을 확인한 뒤에만 요약을 올린다 (`t2k user ready`). Worker 의 조작 API(`/api/ops`)는 Access 를 거친 요청만 받고, `t2k user ready` 는 Access 확인 전에 쌓인 사이트 조작을 버린다. 검색 엔진 수집은 robots.txt · `X-Robots-Tag` · meta 로 막는다.
- **디스코드 지시:** owner 로 등록한 사람이 자기 채널에 쓴 글만 지시로 받는다. Claude 는 그 사람의 파일 사본만 있는 임시 작업 공간(`/private/tmp/t2k-agent`)에서 실행하며, 셸 · 웹 도구와 홈 · 시스템 폴더의 읽기 · 쓰기를 거부 규칙으로 막는다. 맥의 전역 Claude Code 설정과 MCP 서버도 적용하지 않는다. 결과 중 그 사람의 파일만 반영하고, 사람별 템플릿은 Jinja 샌드박스에서 렌더링한다. 적용은 「승인」 뒤에만 한다.
- **요약:** 영상의 자막 · 설명란은 신뢰하지 않는 데이터로 다룬다 (프롬프트의 보안 규칙). 관련 링크는 설명란과 게시자 댓글에 실제로 있는 주소만 쓴다. 영상이 이름만 말한 GitHub 저장소는 프로그램이 GitHub 검색으로 찾되, 이름이 정확히 같고 별이 500개 이상이며 같은 이름의 다른 저장소보다 별이 5배 이상 많을 때만 쓰고 「추정」으로 표시한다 (`t2k/repo_search.py`).
- **비밀:** 디스코드 토큰은 `~/.config/t2k/discord.env`(권한 600)에, Cloudflare 로그인 정보는 wrangler 가 보관한다. 데이터 폴더와 이 저장소에는 비밀을 두지 않는다. 자막 원문(`transcripts/`)은 커밋하지 않는다.

## 구성

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/ko/architecture-dark.png">
  <img alt="t2k 구조: 가족이 디스코드에 올린 링크를 맥의 t2k 가 읽어 YouTube 자막을 추출하고 Claude Code 로 요약한 뒤, 데이터 저장소에 커밋하고 Cloudflare Workers 에 배포한다. 사이트는 Cloudflare Access 로 로그인한 가족만 열람하며, 사이트에서 한 조작은 KV 대기열을 거쳐 5분마다 반영된다." src="images/ko/architecture-light.png">
</picture>

| 모듈 | 하는 일 |
|---|---|
| `pipeline.py` | 1분 주기: 디스코드 확인 → 요약 → 주간 요약 → 사이트 조작 반영 → 커밋 · 배포 → 디스코드 답장 |
| `transcript.py` · `ytdlp_update.py` | yt-dlp 로 자막 · 동영상 정보 확보, yt-dlp 자동 업데이트 |
| `summarize.py` · `prompts/<lang>/summarize.txt` | Claude 요약 (JSON 형식 검증, 음악 형식) |
| `weekly.py` · `prompts/<lang>/weekly.txt` | 주간 요약 (매주 일요일 자정, 카테고리별) |
| `site.py` · `site_templates/` · `site_assets/` | 정적 사이트 생성 |
| `deploy.py` · `worker/index.js` · `site_ops.py` | Workers 배포, 사이트 조작 대기열(KV) |
| `discord_inbox.py` · `bot.py` | 디스코드 읽기 · 답장, 실시간 봇 |
| `agent.py` | 디스코드 지시 처리 (작업 공간, 승인 흐름) |
| `config.py` · `defaults.toml` | 설정과 사람별 경로 |
| `setup.py` · `service.py` · `doctor.py` | 설치 명령 · 자동 실행 등록 · 점검 |

안내서와 README 의 도식은 `docs/diagrams/` 에서 생성한다. 구조 도식은 [archify](https://github.com/tt-a1i/archify) 사양(`architecture.json`)으로, 처리 흐름과 지시 흐름은 diagram-design 양식의 생성 스크립트(`build.py`)로 만든다.
