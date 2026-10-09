<div align="center">

<img src="assets/hero.svg" alt="Clover Mail — understand your inbox, keep control" width="100%">
<h1>Clover Mail</h1>
<p><a href="https://github.com/clover475/clover-mail/actions/workflows/ci.yml"><img src="https://github.com/clover475/clover-mail/actions/workflows/ci.yml/badge.svg?branch=main" alt="Synthetic tests"></a></p>
<p><strong>Understand every inbox from Apple Mail. Work from Feishu. Keep an archive on your Mac.</strong></p>
<p>一个面向 macOS 的个人 AI 邮件中枢：Apple Mail 只读导入，MiMo 生成中文理解，飞书处理邮件，本机长期检索。</p>
<p><a href="docs/setup.md">Get started</a> · <a href="docs/privacy.md">Privacy model</a> · <a href="docs/architecture.md">Architecture</a> · <a href="LICENSE">MIT license</a></p>

</div>

---

### One inbox workflow, across accounts

```text
Apple Mail (read-only) ──→ local SQLite archive ──→ MiMo analysis
                              │                        │
                              ├── local search          ├── Feishu Mail Center
                              │                        ├── daily brief → Feishu + email
                              │                        └── optional private Notion mirror
                              └── raw MIME kept on Mac
```

Clover Mail reads messages already cached by Apple Mail. It does not need your
Apple ID or mailbox password and does not send, move, delete, or mark source
messages. Every imported message gets a local identity and a row in Feishu,
including mail still awaiting analysis. The AI layer adds a Chinese title,
summary, full readable-text translation, concrete actions, explicit deadlines,
and image coverage notes. You can set **未处理 / 处理中 / 已完成 / 忽略** in Feishu;
those states sync back to the local archive.

![Illustrative Mail Center with fictional messages](assets/mail-center.svg)

<sub>Illustrative interface with synthetic mail. The actual Feishu Base is created in your own workspace.</sub>

### What works today

| Capability | Current behavior |
| --- | --- |
| Multiple accounts | Reads cached Apple Mail `.emlx` files across accounts; shows source account and mailbox. |
| Incremental sync | First scan defaults to 30 days; subsequent scans overlap by 3 days and deduplicate. |
| AI understanding | MiMo extracts Chinese summary, full readable-text translation, actions, dates, and searchable facts. |
| Newsletter images | Uses embedded/attached images selectively. Remote images are **off by default**; optional selective fetching has limits and tracking risk. |
| Feishu Mail Center | Creates a Base with Today and account views, full readable text, translation, status, and deadline fields. |
| Daily Brief | Synthesizes priority items and actions, then sends to private Feishu chat and SMTP email. |
| Historical search | Local full-text search plus a MiMo-assisted query planner from the CLI. |
| Optional Notion | Mirrors readable original text and translation to a private database you authorize. |

### Quick start

Requires **macOS**, **Python 3.11+**, Apple Mail with locally downloaded mail,
a MiMo API key, and a Feishu self-built app. Gmail/SMTP and Notion are optional.
The Apple Mail index is undocumented, so first check your own macOS version.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .

mkdir -p "$HOME/.config/clover-mail"
cp config/runtime.example "$HOME/.config/clover-mail/runtime.env"
chmod 600 "$HOME/.config/clover-mail/runtime.env"
# Edit the private file locally with your real credentials.
export CLOVER_MAIL_ENV_FILE="$HOME/.config/clover-mail/runtime.env"

.venv/bin/clover-mail doctor
.venv/bin/clover-mail sync --days 30
.venv/bin/clover-mail prepare --limit 5
.venv/bin/clover-mail analyze --limit 5
.venv/bin/clover-mail feishu-setup
.venv/bin/clover-mail feishu-publish --limit 20
```

**Read the [setup guide](docs/setup.md) before enabling Full Disk Access or
scheduling.** It covers Feishu scopes, private credentials, a one-message
validation, SMTP, Notion, and launchd. The public repository contains no real
mail, credentials, or private history.

### Ask your archive

```sh
.venv/bin/clover-mail search 'registration' --days 30
.venv/bin/clover-mail ask '最近两周有哪些需要处理但还没完成的事？'
.venv/bin/clover-mail brief-preview
```

`ask` sends the question to MiMo to plan a search; matching mail is retrieved
from the local archive. A Feishu chat bot is **not** part of this release.
Connecting ChatGPT or Claude to the optional Notion database is a separate
permission and indexing step; copying mail to Notion alone does not give either
assistant automatic access.

### Scope and limits

- **Alpha, personal use:** no hosted service, web dashboard, or account setup wizard.
- Full translation covers text extracted from MIME/HTML. It does not promise
  to read PDF, Office, or every image attachment; unsupported or uncached
  content is reported as incomplete.
- Source-account labels are heuristics based on Apple Mail mailbox URLs. Check
  them on a mixed-account setup before relying on a filtered view.
- A live run needs your own macOS, API credentials, Feishu permissions, and
  optional SMTP/Notion setup. CI checks synthetic fixtures only.

Built for a simple daily routine: **Mail collects → Clover understands → Feishu
helps you act → local search remembers.**

## License

[MIT](LICENSE). Contributions and security reports are described in
[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
