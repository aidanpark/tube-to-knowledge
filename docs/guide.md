# tube-to-knowledge guide

[← README](../README.md) · English | [한국어](guide.ko.md)

Manual installation, everyday use, troubleshooting, security, and how the pieces fit together. To have an AI coding agent install t2k for you, see [AGENT_INSTALL.md](../AGENT_INSTALL.md).

## Before you start

| You need | Why |
|---|---|
| **A Mac that stays on** (Mac mini or similar, macOS) | It checks Discord and summarizes every minute. Background jobs (launchd) and Discord requests are macOS-only |
| **A home internet connection** | YouTube often blocks caption requests from cloud servers. t2k assumes a Mac on a home connection |
| **A Claude subscription or API access** | Summaries (`claude -p`, sonnet by default) and requests (opus by default) run on Claude Code. You pay for your own usage |
| **A Discord account and server** | Your inbox. Free |
| **A Cloudflare account** | The archive sites (Workers) and access control (Access). Personal use fits in the free tier |
| **Basic terminal skills** | Enough to paste and run commands |

The first setup takes about an hour. Each step ends with a **Check**; move on when it passes. Run `t2k doctor` at any time to see what is missing.

> **Note:** the site, summaries, weekly reviews, Discord replies and the command line (including `t2k doctor`) follow `[general] language` in your data folder's `config.toml`. The background job logs (`~/Library/Logs/t2k/`) and the progress lines of `t2k sync-ops` and `t2k weekly` are currently in Korean. Before `t2k init` creates `config.toml`, the command line uses English, or Korean with `T2K_LANG=ko`; once `config.toml` exists, `T2K_LANG` is ignored.

> **Terms of use:** t2k fetches YouTube captions with yt-dlp. This may conflict with YouTube's Terms of Service, so use it for personal purposes and at your own risk. Summaries contain other people's content, so keep every site private with Access (step 5). t2k publishes no summaries until Access is confirmed.

## Install

### 1. Install the tools

Install [Homebrew](https://brew.sh), then run:

```sh
brew install uv yt-dlp deno node git
```

| Tool | Used for |
|---|---|
| uv | Python 3.12 environment (uv installs it, pinned by `.python-version`) and runs |
| yt-dlp | Captions and video information. If fetching keeps failing, t2k checks for a new release and runs `brew upgrade yt-dlp` |
| deno | The JavaScript runtime yt-dlp uses for YouTube |
| node (npx) | Runs wrangler, Cloudflare's deploy tool (`npx wrangler@4.147.0`, pinned) |

Install **Claude Code** (https://claude.com/claude-code) and run `claude` once in a terminal to sign in (`/login`). Sign in this way rather than with an API key in your shell: the background jobs do not see your shell's environment variables, so an exported `ANTHROPIC_API_KEY` works in the terminal but not for t2k. `t2k doctor` checks Claude with the same environment the background jobs get.

### 2. Get t2k and create your data folder

t2k keeps the **code** (this repository) and your **data** (summaries and settings, `~/t2k-data` by default) in separate folders. The data folder is a git repository, so every change is recorded.

```sh
git clone https://github.com/aidanpark/tube-to-knowledge.git ~/tube-to-knowledge
cd ~/tube-to-knowledge
uv sync                           # install dependencies (.venv)
uv run pytest -q                  # tests; no external services are called
echo 'alias t2k="$HOME/tube-to-knowledge/.venv/bin/t2k"' >> ~/.zshrc && source ~/.zshrc
t2k init                          # creates ~/t2k-data
```

- To keep your data elsewhere, add `export T2K_HOME=<folder>` to `~/.zshrc` before running `t2k init`.
- **Language:** `t2k init` writes `[general] language = "en"` into `config.toml`. For Korean, run `T2K_LANG=ko t2k init` instead (it writes `"ko"`), or change the line. Choose the language before adding people: each person's first category and interests file are created in it.
- To back up your data, create a **private** remote repository, connect it with `git -C ~/t2k-data remote add origin <url>`, and push once from a terminal with `git -C ~/t2k-data push -u origin main` (this also stores your git credentials). After that, t2k pushes after every change.
- If `t2k init` reports that it could not commit, set your git identity and run it again: `git config --global user.name "Name"`, `git config --global user.email "you@example.com"`.

**Check:** in `t2k doctor`, the Tools and Data folder sections pass, except for the subdomain and people you have not added yet.

### 3. Create the Discord bot

1. Go to https://discord.com/developers/applications → **New Application** and give it a name (for example, t2k).
2. On the **Bot** page:
   - Turn on **Privileged Gateway Intents → Message Content Intent** and save. Without it, links and messages arrive empty.
   - Click **Reset Token**, then **Copy** the new token. It is shown only once.
3. Save the token to a file. The command below saves the token from your clipboard, so **type it in rather than pasting it** (copying the command would replace the token on your clipboard).
   ```sh
   mkdir -p ~/.config/t2k
   (umask 077; printf 'DISCORD_BOT_TOKEN=%s\n' "$(pbpaste)" > ~/.config/t2k/discord.env); pbcopy < /dev/null
   ```
   Treat the token like a password. Never put it in a chat or a repository.
4. Invite the bot to your server. If you do not have one, create it in the Discord app first (**+** on the left → Create My Own).
   In the developer portal, open **OAuth2 → URL Generator**, select the scope **bot** and the permissions **View Channels · Send Messages · Read Message History · Add Reactions · Embed Links**, then open the generated URL in a browser and choose your server.

**Check:** in `t2k doctor`, the Discord section shows the bot's name and Message Content Intent passes.

### 4. Discord channels and IDs

1. In Discord, open Settings → Advanced and turn on **Developer Mode**. Right-clicking a channel or person now shows **Copy ID**.
2. Invite your family to the server: server name → Invite People. Check Discord's minimum age for your country.
3. Create one **private channel** per person (for example, `archive-alice`): Create Channel → turn on Private Channel → add that person and the bot. Under the channel's permissions, allow the person **Read Message History · Embed Links · Add Reactions** (without them, they cannot see the bot's replies later).
   From the second channel on, right-click the first one and choose **Duplicate Channel** to keep the private setting and bot permissions.
4. Copy each channel's **Channel ID**, and the **User ID** of everyone who may make requests in it (the person and the administrator).

### 5. Cloudflare and people

#### 5-1. Sign in and find your subdomain

```sh
npx --yes wrangler@4.147.0 login     # sign in to Cloudflare in the browser and allow access
```

Open the Cloudflare dashboard → **Workers & Pages** to see your account's workers.dev subdomain (you will be asked to choose one the first time). In your data folder's `config.toml`, `t2k init` already wrote a `[site]` table with an empty `subdomain = ""` line. Fill in that line; do not add a second `[site]` table:

```toml
subdomain = "myname"     # sites live at https://<id>-yt.myname.workers.dev
```

> **Warning:** never run wrangler commands directly in your data folder or in any folder that contains `site/`. Some wrangler commands publish the current folder's `site/` without protection. t2k always runs wrangler from an empty temporary folder.

#### 5-2. Add a person

```sh
t2k user add --id alice --name Alice --channel <channel ID> --owner <Alice's user ID> --owner <your user ID>
```

- `--id` uses lowercase letters and digits. It becomes the folder name and part of the site address.
- `--owner` is someone who may make **requests** in this channel. Anyone in the channel can share links, but only owners can make requests.
- This creates the person's folder (`users/alice/`: one category, "Other", and an interests file `profile.md`) and the site's edit queue (KV), and deploys **a site with a single "coming soon" line**. No summaries are published yet.
- If it stops halfway (for example, wrangler is signed out or the placeholder deploy fails), fix the problem and run the same command again. It continues with what was already recorded.

#### 5-3. Lock the site with Access

1. In the Cloudflare dashboard, open **Zero Trust**. The first time, choose a team name and the **Free** plan (some accounts are asked for a payment method).
2. Zero Trust → Access controls → **Policies → Add a policy**: a name (for example, "Alice and admin"), Action **Allow**, and under Include **Emails**, the addresses that may open this site.
3. Dashboard → Workers & Pages → `alice-yt` → **Protect this Worker behind Access** (also under Settings → Domains & Routes, on the workers.dev entry) → Scope **All traffic** → choose the policy from step 2 → apply.

Cloudflare renames screens from time to time (as of October 2026). If you cannot find them, search the dashboard for "Access".

#### 5-4. Open the site

```sh
t2k user ready alice
```

t2k first checks that the site redirects to the Access sign-in page (302). Only then does it set `ready = true` and publish the archive. If the check fails, nothing is published.

Repeat 5-2 to 5-4 for each person. `t2k user list` shows everyone.

**Check:** in `t2k doctor`, the Cloudflare section shows `302 → Access login` for every site. **If it says the site opens without Access, fix the Access setup immediately.**

### 6. Run in the background

```sh
t2k service install
```

This registers three macOS launchd jobs. They start automatically when you sign in after a restart.

| Job | What it does | Runs | Log (`~/Library/Logs/t2k/`) |
|---|---|---|---|
| `com.<user>.t2k.pipeline` | Reads links, summarizes, publishes; writes weekly reviews after Sunday midnight | Every minute | `pipeline.log` |
| `com.<user>.t2k.agent` | Handles Discord requests | Every minute, and when the bot wakes it | `agent.log` |
| `com.<user>.t2k.bot` | Stays connected to Discord to receive requests instantly | Always | `bot.log` |

Use `t2k service status` to check them and `t2k service uninstall` to remove them.

**Check:** `t2k doctor` ends with `No problems found`. In the YouTube app, open a video → Share → Discord → your channel. 👀 appears within a minute, and ✅ with the summary link a few minutes later.

## Usage

- **Save a video:** send a YouTube link to your channel. If your channel does not appear in the share sheet, search for its name there.
- **Make a request:** write a message without a link ([Change your site by asking](../README.md#change-your-site-by-asking)).
- **Edit on the site:** move or delete a summary at the bottom of its page, and add categories on the Categories page. Changes apply within a few minutes.
- **Manual commands:** `t2k summarize <URL> --user alice` (summarize now and publish to an open site), `t2k weekly 2026-W42` (write and publish that week's reviews; defaults to last week), `t2k redeploy` (republish sites), `t2k sync-ops` (apply site edits now), `t2k discord-check`.

### Per-person files (`<data folder>/users/<id>/`)

| File | Contents |
|---|---|
| `profile.md` | Interests, used for personal insights and picks |
| `rules.md` | How you want summaries written (for example, list ingredients for cooking videos) |
| `categories.toml` | Categories (name and description). The description is Claude's filing rule |
| `templates/` · `style.css` | The site's look. Files here override the default templates (`t2k/site_templates/`) with the same name; the CSS is appended |
| `summaries/` | Summaries. Committed |
| `weekly/` | Weekly reviews (`<YYYY-Www>/<category id>.json`). Committed |
| `site/` · `state/` | The generated site and processing state. Not committed |

Default settings live in `t2k/defaults.toml`. To change one, set the same table and key in your data folder's `config.toml` (for example, `model = "haiku"` under `[summary]`). `[summary] languages` sets the caption language priority; when empty it follows `[general] language`.

## Update

```sh
cd ~/tube-to-knowledge && git pull && uv sync && t2k service install && t2k doctor
```

## Troubleshooting

| Symptom | Check / fix |
|---|---|
| No 👀 | The Discord section of `t2k doctor`, and whether `bot.log` shows a connection. If you reset the token, update `discord.env` and run `t2k service install` |
| Links get no reaction and the log shows empty messages | Message Content Intent is off (step 3) |
| The log says t2k is pausing because of a block | YouTube is blocking caption requests from your home IP (429). t2k retries after 30 minutes, 1, 2 and 4 hours. After 4 hours, the site's home page shows a notice |
| A `claude -p` error | Claude Code is signed out or you hit a usage limit. Run `claude` in a terminal. If it says `claude not found in PATH`, run `t2k service install` again |
| An edit-queue error with `[code: 10000]` | A temporary error right after the wrangler sign-in refreshes. t2k retries. If it persists, run `npx --yes wrangler@4.147.0 login` |
| Site deploy fails | Look for the deploy failure in `pipeline.log`. t2k retries on the next run. Manually: `t2k redeploy <id>` |
| A family member cannot see the bot's earlier replies | They lack Read Message History in their channel (step 4) |
| A request gets no response | Check `agent.log`. If one of that person's requests is waiting for approval (⏳), the next one waits until they approve or cancel |
| A wrong channel or owner ID | Edit that person's `[[user]]` entry (`channel_id`, `owners`) in `config.toml`, then run `t2k service install` so the always-on bot reloads it. `t2k doctor` shows whether each channel can be read |
| Every command stops with "Cannot read …config.toml" | The file has a syntax error, often a second `[site]` table. Fix the line the message names |
| `t2k user add` stopped halfway | Fix the problem in the message and run the same command again; it continues |

## Security

- **Sites:** only the emails you allow in Cloudflare Access can open a site. A new site starts as an empty placeholder, and summaries are published only after t2k confirms the redirect to the Access sign-in (`t2k user ready`). The Worker's edit API (`/api/ops`) accepts only requests that passed Access, and `t2k user ready` discards any site edits queued before Access was confirmed. Search engines are kept out with robots.txt, `X-Robots-Tag` and a meta tag.
- **Discord requests:** only messages from registered owners in their own channel count as requests. Claude runs in a temporary workspace (`/private/tmp/t2k-agent`) holding a copy of that person's files only, with shell and web tools and reads/writes to your home and system folders denied. Your global Claude Code settings and MCP servers are not loaded. Only that person's files are taken from the result, per-person templates render in a Jinja sandbox, and nothing is applied before approval.
- **Summaries:** captions and video descriptions are treated as untrusted data (security rules in the prompt). Related links are limited to URLs that actually appear in the description.
- **Secrets:** the Discord token lives in `~/.config/t2k/discord.env` (mode 600) and the Cloudflare sign-in is kept by wrangler. Neither the data folder nor this repository holds secrets. Raw captions (`transcripts/`) are never committed.

## Project layout

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/en/architecture-dark.png">
  <img alt="Architecture: family members share links in Discord; t2k on a Mac reads them, gets captions from YouTube, summarizes with Claude Code, commits to a data repository and deploys to Cloudflare Workers. Each site is behind Cloudflare Access; edits made on the site come back through a KV queue every 5 minutes." src="images/en/architecture-light.png">
</picture>

| Module | What it does |
|---|---|
| `pipeline.py` | The one-minute cycle: check Discord → summarize → weekly review → apply site edits → commit and deploy → reply in Discord |
| `transcript.py` · `ytdlp_update.py` | Captions and video information via yt-dlp; yt-dlp auto-update |
| `summarize.py` · `prompts/<lang>/summarize.txt` | Claude summaries (JSON validation, music format) |
| `weekly.py` · `prompts/<lang>/weekly.txt` | Weekly reviews (Sunday midnight, per category) |
| `site.py` · `site_templates/` · `site_assets/` | Static site generation |
| `deploy.py` · `worker/index.js` · `site_ops.py` | Workers deploys and the site edit queue (KV) |
| `discord_inbox.py` · `bot.py` | Reading and replying in Discord; the always-on bot |
| `agent.py` | Discord requests (sandbox, approval flow) |
| `i18n.py` · `locales/<lang>/` | Display language |
| `config.py` · `defaults.toml` | Settings and per-person paths |
| `setup.py` · `service.py` · `doctor.py` | Setup commands, background jobs, checks |

The diagrams in this guide and the README are generated in `docs/diagrams/`: the architecture diagram from an [archify](https://github.com/tt-a1i/archify) spec (`architecture-<lang>.json`), and the two flow diagrams by a diagram-design style script (`build.py`).
