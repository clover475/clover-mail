<div align="center">

<img src="assets/hero.svg" alt="Clover Mail — your inbox, understood" width="100%">

<h1>Clover Mail</h1>

<p><strong>Turn your inbox into a private, searchable AI memory.</strong></p>

<p>
<a href="https://github.com/clover475/clover-mail/actions/workflows/ci.yml"><img src="https://github.com/clover475/clover-mail/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
<img src="https://img.shields.io/badge/platform-macOS-lightgrey.svg" alt="macOS">
<img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
</p>

<p><a href="README.zh-CN.md">中文说明</a> · <a href="docs/mcp.md">MCP guide</a> · <a href="docs/setup.md">Setup</a> · <a href="docs/privacy.md">Privacy</a> · <a href="docs/architecture.md">Architecture</a></p>

</div>

Clover Mail copies the mail that Apple Mail has already downloaded on your Mac
into a local SQLite archive. From there you can search it, have a model of your
choice summarize it, and let AI assistants such as Codex or Claude query it
through a **read-only** [Model Context Protocol](https://modelcontextprotocol.io)
server.

> **Status: alpha (0.2).** In real use by its maintainer on macOS. Expect rough
> edges, and read [Limitations](#limitations) before relying on it.

## Why

- **One memory for every inbox.** School, work and personal accounts already
  meet in Apple Mail. Clover Mail reads that local cache, so it never asks for
  your mail passwords.
- **Your archive stays on your Mac.** The archive is a local file. Cloud services
  see data only when you turn on a feature that sends it, and every such feature
  is off by default.
- **Bring your own AI.** The archive is not tied to a model vendor or a chat
  client. Swap either one without touching your data.
- **Ask in plain language.** "What do I still need to reply to?" from Codex or
  Claude, without copying and pasting mail.

## How it works

```text
Apple Mail local cache  (read-only: never sends, moves, deletes or marks mail)
        │  clover-mail sync
        ▼
Local SQLite archive  ~/Library/Application Support/CloverMail/
        ├── keyword and natural-language search (CLI)
        ├── read-only MCP server ──► Codex · Claude · other MCP clients
        ├── optional AI analysis   (summary, translation, to-dos, deadlines)
        ├── optional daily brief   (stored locally; Feishu or email delivery)
        └── optional mirrors       (Feishu Base, Notion)
```

## Features

| Feature | What it does | Needs |
| --- | --- | --- |
| Local archive | Imports downloaded Apple Mail messages, de-duplicated; raw MIME stays local | Full Disk Access for the Python process |
| Search | Matches subject, sender, body and AI fields; `ask` turns a question into a search | `ask` needs an AI provider |
| AI analysis | Chinese summary and translation, action items, deadlines, image-aware reading | An AI provider you choose |
| MCP server | Six read-only tools for AI assistants (see below) | `pip install -e '.[mcp]'` |
| Daily brief | A stored daily digest, optionally sent to Feishu or by email | AI provider; Feishu or SMTP to deliver |
| Mirrors | Feishu Base mail center with editable handling status; Notion full-text mirror | Your own Feishu app or Notion integration |
| Scheduler | `launchd` job that syncs and analyzes every 15 minutes | Optional |

**Mail accounts.** Anything Apple Mail syncs: Gmail, Outlook / Microsoft 365
(including many school and work accounts), iCloud and other IMAP accounts.
Gmail and an Outlook-based university account are in daily use; other
providers should work but are less tested.

**AI providers.** MiMo, any OpenAI-compatible Chat Completions endpoint, or
Anthropic Messages. AI is optional: archive, search and MCP work without it.

## Quick start

Requirements: macOS, Apple Mail with at least one downloaded message, and
Python 3.11 or newer.

```sh
git clone https://github.com/clover475/clover-mail.git
cd clover-mail
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mcp]'

.venv/bin/clover-mail doctor           # counts accounts and mailboxes; prints no mail content
.venv/bin/clover-mail sync --days 3    # first real import: start small
.venv/bin/clover-mail search 'invoice' --days 3
.venv/bin/python scripts/check_mcp.py  # starts the MCP server and checks its tools
```

`doctor` needs **Full Disk Access** for the terminal you run it in
(System Settings → Privacy & Security). That is a broad permission; grant it
only to the app you actually use. Then follow the [setup guide](docs/setup.md)
to add an AI provider, a schedule, or the optional integrations.

## Connect an AI assistant

The MCP server runs on your Mac and talks to the client over stdio. Use the
absolute path of the virtual environment created above.

| Client | Status | How |
| --- | --- | --- |
| Codex (CLI, or Codex in ChatGPT Desktop) | ✅ Verified | `codex mcp add clover-mail -- /path/to/clover-mail/.venv/bin/clover-mail-mcp` |
| Claude Code | ✅ Verified | `claude mcp add -s user clover-mail -- /path/to/clover-mail/.venv/bin/clover-mail-mcp` |
| Claude Desktop | Documented, not yet verified | Add an `mcpServers` entry; see the [MCP guide](docs/mcp.md#claude-desktop) |
| ChatGPT Desktop, Work mode | Documented, not yet verified | Personal local plugin; see the [MCP guide](docs/mcp.md) |
| ChatGPT web / regular cloud chat | ❌ Not supported | A cloud chat cannot reach a program on your Mac |

Then ask, for example:

- "Use Clover Mail to summarize my mail from the last 3 days."
- "Did my school send anything important this week?"
- "What mail still needs action from me?"
- "Find the quotation email from the venue and show me its body."
- "Draft a reply to that message." *(the assistant drafts; Clover Mail cannot send)*

### MCP tools

| Tool | Returns |
| --- | --- |
| `mail_list_accounts` | Accounts with message counts and last sync time |
| `mail_get_summary` | Recent mail, important items and upcoming deadlines from cached analysis |
| `mail_search` | Paginated search by keyword, sender, recipient, subject, date and account |
| `mail_get_message` | One message with its analysis; the body only on request, length-capped |
| `mail_get_pending_actions` | Messages whose to-dos are still open |
| `mail_get_daily_brief` | A stored brief, or a clear "not generated" (never invented) |

Every tool is annotated read-only. No tool sends, deletes, moves or edits mail,
and none syncs or calls a model. Accounts appear as opaque IDs until you add an
[optional alias file](docs/mcp.md#optional-account-labels--可选账户别名) with
labels such as `School` or `Gmail`.

## Privacy and security

- **Read-only toward your mail.** Clover Mail never writes to Apple Mail.
- **Local by default.** The archive, credentials (`~/.config/clover-mail/`) and
  alias map stay on your Mac with owner-only permissions.
- **Each cloud feature is opt-in and separate.** AI providers, Feishu, Notion,
  SMTP and remote images each receive only what [Privacy](docs/privacy.md)
  describes, and only after you configure them.
- **Your assistant is a data recipient too.** When Codex, Claude or ChatGPT
  reads a tool result, that content goes to its provider. Running locally is
  not offline inference.
- **Email is untrusted input.** Tool results are marked as data, not
  instructions, to blunt prompt injection. AI-extracted dates and actions can
  still be wrong; check the original message.

Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## Limitations

- macOS and Apple Mail only. Only messages Apple Mail has fully downloaded can
  be imported, and Apple's cache format is undocumented and may change.
- The MCP server is local (stdio). Regular ChatGPT web chat cannot use it.
- Unread state is not stored, so `unread_only` is not supported.
- No encrypted backup or multi-device sync of the archive; rely on FileVault and
  your normal backups.
- The Feishu and Notion adapters assume you create and own the target workspace.

## Roadmap

Planned, roughly in this order. No dates are promised.

1. **Per-source permissions.** Grant each mailbox separately for the local
   archive, AI, Feishu, Notion and email, failing closed when nothing is granted.
2. **Clearer sync reporting.** Count messages skipped for missing permission or
   excluded folders, and recognise localized Gmail and Outlook spam and trash
   folder names.
3. **Resilient scheduled runs.** Skip destinations that are not enabled, keep
   going when one external service fails, and always store the daily brief
   before delivering it.
4. **Simpler install.** A single setup command that can also register the MCP
   server with installed clients.
5. **Optional remote access.** A documented, opt-in route for cloud ChatGPT via
   OpenAI's Secure MCP Tunnel, after a security review.

Suggestions are welcome in [Issues](https://github.com/clover475/clover-mail/issues).

## Contributing

Small, focused pull requests are welcome. Use synthetic mail only: never attach
real messages, archives, credentials or inbox screenshots. Start with
[CONTRIBUTING.md](CONTRIBUTING.md).

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

## License

[MIT](LICENSE) © Clover Mail contributors
