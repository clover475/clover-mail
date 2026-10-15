# Security

Clover Mail handles private correspondence. Use synthetic examples in all
issues and pull requests. Never share real mail, tokens, local database files,
archive paths, inbox screenshots, private Feishu/Notion links, account-alias
maps, or MCP transcripts containing mail.

## Report a vulnerability privately

Use GitHub's private reporting: open the repository's **Security** tab and
choose **Report a vulnerability**. If that button is not available, contact the
maintainer through the GitHub profile that owns this repository and ask for a
private channel. Do not open a public issue with an exploit, credential, or
private data. Include a concise impact description, reproduction steps with
synthetic data, the affected version or commit, and a safe proof of concept.

We aim to acknowledge a report within 7 days. This is a volunteer-maintained
alpha, so fixes are best effort; please allow a reasonable period before public
disclosure.

## Supported versions

Only the latest commit on `main` receives security fixes during the alpha.

Repository visibility and GitHub security features are administrator settings
that can change. Do not assume secret scanning, push protection, or private
vulnerability reporting is enabled; check the current repository settings.

## Trust boundaries

- **Apple Mail and local archive:** Apple Mail is read-only; raw MIME and
  attachments remain in the local SQLite archive. The macOS process may need
  Full Disk Access, which is broad filesystem permission.
- **Optional cloud adapters:** selected AI providers, Feishu, Notion, SMTP, and
  remote image hosts receive only the data described in [Privacy](docs/privacy.md)
  when their matching feature runs. Their accounts and retention policies are
  separate trust boundaries.
- **Optional local MCP:** `clover-mail-mcp` opens the archive read-only and
  exposes no write, sync, analysis, or network-listening tool. A configured
  desktop MCP client can still request sensitive mail content, so it must be
  trusted. See [MCP](docs/mcp.md).
- **Local assistant clients:** Codex, Claude Code, Claude Desktop and the
  optional ChatGPT Desktop personal plugin all launch the same read-only server
  on the Mac. Tool results they request are sent to that client's model
  provider, so only connect clients whose data handling you accept.
- **Optional ChatGPT tunnel:** Secure MCP Tunnel is a separately configured
  outbound service that requires its own permissions and API key. It is not
  installed or exercised by this repository.

Email content and AI-derived fields are untrusted input. Verify important
actions and deadlines against the original message. See [Privacy](docs/privacy.md)
for operational data-flow detail.
