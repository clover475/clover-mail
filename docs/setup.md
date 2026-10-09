# Set up Clover Mail on macOS

This is a local-first alpha. Complete each check manually before scheduling;
the scheduled installer deliberately requires a successful live run.

## 1. Prepare Apple Mail and Python

Open Apple Mail, sign in to your mail providers there, and allow messages to
download. Clover Mail reads the existing local cache; it does not manage
account sign-in. Install Python 3.11 or newer and, in the cloned repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Grant **Full Disk Access** in macOS System Settings → Privacy & Security to
the terminal application used for the initial checks. For launchd later, grant
it to the exact `.venv/bin/python` interpreter used by the LaunchAgent.
This is a broad system permission; see [Privacy](privacy.md).

```sh
.venv/bin/clover-mail doctor
```

`doctor` prints account, mailbox and index counts, without mail content. A
schema or permission error should be resolved before proceeding. Apple does
not publish this index as a stable API, so a future macOS version may require
an adapter update.

## 2. Put credentials outside Git

Create a private runtime file and set an IANA timezone. Do not paste keys
into a GitHub issue or chat.

```sh
mkdir -p "$HOME/.config/clover-mail"
cp config/runtime.example "$HOME/.config/clover-mail/runtime.env"
chmod 600 "$HOME/.config/clover-mail/runtime.env"
export CLOVER_MAIL_ENV_FILE="$HOME/.config/clover-mail/runtime.env"
```

Run `.venv/bin/python scripts/configure_ai.py` in a local terminal to choose
MiMo, OpenAI-compatible, or Anthropic, then enter the API key at the hidden
prompt. The script writes the selected provider, endpoint, model ID, and key
to `~/.config/clover-mail/runtime.env` with owner-only permissions. Choose the
model ID shown in your provider's console. OpenAI-compatible endpoints must
implement Chat Completions and support image inputs if you want image analysis.
For advanced setups, edit the `CLOVER_MAIL_AI_*` fields manually. Legacy
`MIMO_*` variables remain supported for existing MiMo installations.

The template also has `CLOVER_MAIL_TIMEZONE=UTC`. Change it to your local IANA
zone, for example `Asia/Singapore`, before relying on the 21:00 Brief.

## 3. Validate the local path

```sh
.venv/bin/clover-mail sync --days 30
.venv/bin/clover-mail prepare --limit 5
.venv/bin/clover-mail analyze --limit 1
.venv/bin/clover-mail search 'example topic' --days 30
```

`prepare` is a no-model preview. `analyze` sends the selected message's
extracted content to the selected AI provider. The first scan defaults to 30 days if `--days` is
omitted; later runs rescan a short overlap and deduplicate. The archive lives
under `~/Library/Application Support/CloverMail/` with private permissions.

## 4. Create the Feishu Mail Center

Create a **self-built Feishu app** in your workspace and place its app ID,
secret, and your own Open ID in the private runtime file. Enable app identity
access for Feishu Base creation and records (`bitable:app`) and sending an
app message to your Open ID. Publish the app permissions in the developer
console. Exact console wording can change; API errors report a missing scope.
Keep the generated Base private and give only intended people edit access.

```sh
.venv/bin/clover-mail feishu-setup
.venv/bin/clover-mail feishu-publish --limit 20
.venv/bin/clover-mail feishu-pull-status
```

`feishu-setup` returns the Today view URL. Open it yourself and confirm a
synthetic or low-risk validation row before bulk publishing. Every archived
message is copied to Feishu, even if AI has not analyzed it yet. Its row
says **待分析** until analysis completes. The default views include Today,
Outlook and Gmail; every row also retains source account and mailbox. Source
labels use Apple Mail mailbox URL heuristics, so verify them for your setup.

Edit a row's **处理状态** in Feishu, run `feishu-pull-status`, and confirm it is
reflected locally. Feishu is the state authority; Notion is a one-way mirror.

## 5. Optional: SMTP Daily Brief

Set the `CLOVER_MAIL_SMTP_*` and `CLOVER_MAIL_EMAIL_*` fields in the private
runtime file. SMTP uses TLS on port 465. For Gmail, create an application
password in your Google account and run `python3 scripts/configure_gmail.py`
in a local interactive terminal to enter it without echoing. The script writes
to `~/.config/clover-mail/runtime.env` with owner-only permissions.

After there is analyzed mail for today, validate both delivery channels:

```sh
.venv/bin/clover-mail brief-preview
.venv/bin/clover-mail brief-generate
.venv/bin/clover-mail brief-show
.venv/bin/clover-mail brief-send-feishu
.venv/bin/clover-mail brief-send-email
```

The Brief is an AI synthesis of extracted facts. It marks mail still waiting
for analysis and warns when remote images were not loaded. A later evening
pass can replace an early validation Brief once if facts changed.

## 6. Optional: private Notion mirror

Create a private Notion database with these properties. Share only that
database with a dedicated integration that can read and update content. Put
its token and the database **data source ID** in the runtime file as
`CLOVER_MAIL_NOTION_TOKEN` and `CLOVER_MAIL_NOTION_DATA_SOURCE_ID`.

| Property | Type |
| --- | --- |
| 中文标题 | Title |
| 本地邮件ID, 发件人, 中文摘要, 待办, 原文标题, 邮箱文件夹 | Rich text |
| 来源邮箱, 处理状态, AI理解状态 | Select |
| 收件日期, 截止日期 | Date |

```sh
.venv/bin/clover-mail notion-doctor
.venv/bin/clover-mail notion-publish --limit 1
```

Open that one page and check its extracted original text and translation
before mirroring more. Notion will contain readable full text, so understand
the added cloud exposure in [Privacy](privacy.md). It does not upload raw MIME
or attachments. Notion content alone does not automatically grant ChatGPT or
Claude access; each assistant requires its own authorized connection and
indexing.

## 7. Enable the per-user schedule

After one real import, analysis, Feishu publish, and successful Brief on both
channels, install the full LaunchAgent:

```sh
.venv/bin/clover-mail install-launch-agent
```

It runs every 15 minutes, limits AI analysis to a small batch per pass,
updates Feishu and optional Notion, and sends the Daily Brief after 21:00 in
your configured timezone. Inspect
`~/Library/Application Support/CloverMail/runner.log` and
`runner-error.log` if a scheduled run fails. On a Mac without SMTP, the
`install-local-launch-agent` command schedules only sync, analysis, Feishu,
and optional Notion after a real import and analysis.

The LaunchAgent records the Python executable and repository path used during
installation. Keep that interpreter and checkout in place. To stop it, use
`launchctl bootout gui/$(id -u) "$HOME/Library/LaunchAgents/com.clover.mail.plist"`.
