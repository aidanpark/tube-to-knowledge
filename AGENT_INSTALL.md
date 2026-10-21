# Installing tube-to-knowledge with an AI coding agent

This file is for an AI coding agent (Claude Code or similar) that a person has asked to set up tube-to-knowledge (`t2k`) on their Mac. It follows the same steps as the [manual installation guide](docs/guide.md#install), split into what you (the agent) do and what only the person can do.

The person starts you with something like:

> Set up tube-to-knowledge on this Mac by following AGENT_INSTALL.md in https://github.com/aidanpark/tube-to-knowledge. Stop and ask me whenever a step needs my account, a token or a browser login.

## Rules

Follow these for the whole installation. They protect the person's accounts and keep their archive private.

1. **Never handle secrets.** Do not ask for, read, print, copy or store the Discord bot token, Cloudflare credentials or any password. The person saves the token themselves with the command in step 4. To confirm it is in place, run `t2k doctor` and read the Discord section; never open `~/.config/t2k/discord.env`.
2. **Never run wrangler in the data folder** or in any folder that contains `site/`. Some wrangler commands publish the current folder's `site/` without protection. Use `t2k` commands only; they run wrangler from an empty temporary folder. The one exception is `npx --yes wrangler@4.147.0 login`, which the person runs.
3. **Never publish before Access is confirmed.** Do not set `ready = true` in `config.toml` by hand. Only `t2k user ready <id>` may open a site, because it first checks that the site redirects to the Access sign-in.
4. **Never act on the person's accounts.** Do not create accounts, accept terms, approve OAuth screens, or change settings in Discord or Cloudflare. Tell the person exactly what to click and wait.
5. **Do not weaken the request sandbox.** Leave the security settings in `t2k/agent.py` (deny rules, isolation flags, workspace location) as they are.
6. **Ask before changing the system.** Get the person's agreement once before installing Homebrew packages and before `t2k service install`. If your permission settings block a command, show the person the exact command to run themselves.
7. **Check every step** with `t2k doctor` as described below, and do not move on while a required item fails.

In the steps below, **[agent]** marks work you do and **[person]** marks a stop point: explain what is needed, wait for the person to confirm, then continue.

## Step 0 — Ask first

**[agent]** Ask the person, then write down the answers for later steps:

- Display language: English (`en`) or Korean (`ko`). It applies to the sites, summaries, weekly reviews, Discord replies and the command line.
- Where to keep the data folder (default `~/t2k-data`), and whether they want a private remote repository as a backup.
- Who will use it: for each person, a short id (lowercase letters and digits, for example `alice`) and a display name.

## Step 1 — Tools

**[agent]** Check `sw_vers` (macOS is required) and `brew --version`.

**[person]** If Homebrew is missing, the person installs it from https://brew.sh (it asks for their macOS password).

**[agent]** Run:

```sh
brew install uv yt-dlp deno node git
```

**[agent]** Check `claude --version`.

**[person]** If Claude Code is missing or signed out, the person installs it (https://claude.com/claude-code) and runs `claude` once to sign in (`/login`). An `ANTHROPIC_API_KEY` exported in the shell is not enough: the background jobs do not see shell variables. `t2k doctor` checks Claude with the background jobs' environment.

## Step 2 — Code and data folder

**[agent]**

```sh
git clone https://github.com/aidanpark/tube-to-knowledge.git ~/tube-to-knowledge
cd ~/tube-to-knowledge && uv sync && uv run pytest -q
echo 'alias t2k="$HOME/tube-to-knowledge/.venv/bin/t2k"' >> ~/.zshrc
```

Use `~/tube-to-knowledge/.venv/bin/t2k` directly in your own commands, because the alias only works in new shells.

- For a data folder other than `~/t2k-data`, add `export T2K_HOME=<folder>` to `~/.zshrc` and set `T2K_HOME` in your own commands too.
- For Korean, run `T2K_LANG=ko t2k init` (it writes `[general] language = "ko"` and Korean comments); otherwise run `t2k init` (English). After that, `config.toml` decides the language and `T2K_LANG` is ignored. Settle the language now: each person's first category and interests file are created in it during step 6.
- If `t2k init` cannot commit, ask the person for the name and email to use with `git config --global user.name` / `user.email`.
- If they want a backup, the person creates a **private** repository; you add it with `git -C <data folder> remote add origin <url>`. Then the person runs `git -C <data folder> push -u origin main` once in their own terminal, so git stores their credentials. The background jobs cannot answer a password prompt; if git cannot push without one, summaries are saved but not published. t2k sets the upstream on its first push by itself, and `t2k doctor` warns while it is missing.

**Check:** `t2k doctor` shows ✓ in the **Tools** and **Data folder** sections, except the workers.dev subdomain and people, which come later.

## Step 3 — Discord server and channels

**[person]** In the Discord app:

1. Create a server if they do not have one (**+** on the left → Create My Own).
2. Turn on Settings → Advanced → **Developer Mode**.
3. Invite family members to the server, then create one **private channel** per person (for example `archive-alice`) with that person and the bot in it, and allow the person Read Message History · Embed Links · Add Reactions. Duplicating the first channel keeps the private setting.
4. Give you each channel's **Channel ID** and the **User ID** of everyone who may make requests in it (right-click → Copy ID). IDs are not secrets.

## Step 4 — Discord bot

**[person]** Guide the person through it:

1. https://discord.com/developers/applications → **New Application** → name it (for example `t2k`).
2. **Bot** page → turn on **Privileged Gateway Intents → Message Content Intent** → Save.
3. **Reset Token** → **Copy**. Then the person **types** (does not paste) this in their own terminal, which saves the clipboard to the token file and clears the clipboard:
   ```sh
   mkdir -p ~/.config/t2k
   (umask 077; printf 'DISCORD_BOT_TOKEN=%s\n' "$(pbpaste)" > ~/.config/t2k/discord.env); pbcopy < /dev/null
   ```
4. **OAuth2 → URL Generator** → scope **bot** → permissions **View Channels · Send Messages · Read Message History · Add Reactions · Embed Links** → open the URL → choose the server. Then add the bot to each private channel.

**Check:** the **Discord** section of `t2k doctor` shows `✓ Bot: <name>` and `✓ Message Content Intent`. Channel checks pass after step 6.

## Step 5 — Cloudflare sign-in and subdomain

**[person]** The person runs this and approves in the browser:

```sh
npx --yes wrangler@4.147.0 login
```

**[person]** The person opens the Cloudflare dashboard → **Workers & Pages** and tells you the account's workers.dev subdomain (they choose one if asked).

**[agent]** In the data folder's `config.toml`, fill in the existing `subdomain = ""` line under `[site]` (written by `t2k init`). Do not append a second `[site]` table; that makes every command fail with "Cannot read …config.toml":

```toml
subdomain = "<subdomain>"
```

**Check:** the **Cloudflare** section of `t2k doctor` shows `✓ wrangler signed in`.

## Step 6 — Add people

**[agent]** For each person:

```sh
t2k user add --id <id> --name "<name>" --channel <channel ID> --owner <their user ID> --owner <admin user ID>
```

This creates the person's folder, the site's edit queue (KV) and a placeholder site at `https://<id>-yt.<subdomain>.workers.dev`. No summaries are published yet. If it stops halfway (wrangler signed out, placeholder deploy failed), fix the cause and run the same command again; it continues from the recorded entry. If a channel or owner ID turns out to be wrong, edit that person's `[[user]]` entry in `config.toml` (after step 9, also run `t2k service install` so the always-on bot reloads it).

**Check:** `t2k doctor` shows the person under **Data folder**, `✓ <id>: can read channel …` under **Discord**, and their KV under **Cloudflare**. It also shows `<id>: not opened yet (ready = false)`, which is expected until step 8.

## Step 7 — Lock each site with Access

**[person]** In the Cloudflare dashboard (screen names may change over time; searching for "Access" helps):

1. **Zero Trust** → choose a team name and the **Free** plan the first time (some accounts are asked for a payment method).
2. Access controls → **Policies → Add a policy**: Action **Allow**, Include **Emails** = the addresses allowed to open this person's site.
3. Workers & Pages → `<id>-yt` → **Protect this Worker behind Access** → Scope **All traffic** → choose the policy → apply.

Wait until the person confirms this for every site.

## Step 8 — Open the sites

**[agent]** For each person:

```sh
t2k user ready <id>
```

It publishes the archive only after confirming the redirect to the Access sign-in, and it discards any site edits that were queued before Access was in place.

**Check:** the **Cloudflare** section of `t2k doctor` shows `✓ <id>: https://… 302 → Access login` for every site. **If it reports `The site opens without Access!`, stop and have the person fix Access before doing anything else.**

## Step 9 — Background jobs

**[agent]** With the person's agreement:

```sh
t2k service install
```

**Check:** `t2k doctor` shows ✓ for the three jobs under **Background jobs (launchd)** and ends with `No problems found`.

## Step 10 — First summary

**[person]** In the YouTube app, the person shares a video to their Discord channel.

**[agent]** Watch `~/Library/Logs/t2k/pipeline.log` (these logs are currently in Korean). Times follow the Mac's time zone; weekly reviews run after Sunday midnight local time. Within a minute the message gets 👀; a few minutes later the bot replies with ✅ and the summary link. Opening the link asks the person to sign in through Access.

If something fails, use the Troubleshooting table in the [guide](docs/guide.md#troubleshooting). When everything works, give the person a short summary: their site addresses, how to save a video, how to make a request, and that `t2k doctor` is the first thing to run when something looks wrong.
