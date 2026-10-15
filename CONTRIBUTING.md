# Contributing to Clover Mail

Small, focused changes are welcome. This is an early macOS project that
handles private correspondence, so synthetic data and narrow trust boundaries
are part of the contribution contract.

## Before opening an issue

- Describe the observed behavior, expected behavior, Clover Mail version, and
  macOS/Python version. For Apple Mail ingestion, include the Mail version and
  whether the message body was downloaded locally.
- Use synthetic mail only. Never attach real `.eml`/`.emlx` files, SQLite
  archives, credentials, inbox screenshots, private cloud links, account IDs,
  or assistant transcripts containing mail.
- Report a security vulnerability privately as described in [SECURITY.md](SECURITY.md),
  not in a public issue.

## Where to start

The [roadmap in the README](README.md#roadmap) lists the planned work. Comment
on or open an issue before a large change so the design can be agreed first.
Issues about Apple Mail on a macOS version or mail provider we have not tested
are especially useful; describe the mailbox layout, not its content.

## Development setup and tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mcp]'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

The optional MCP extra is required to run its SDK protocol test. The base CLI
does not need it. Tests use synthetic fixtures only; do not point a test at a
real archive.

## Module map

| Area | Main modules |
| --- | --- |
| Apple Mail intake | `apple_mail.py`, `archive.py` |
| Content extraction and AI analysis | `content.py`, `processing.py`, `ai.py` |
| User-facing CLI and local runtime | `cli.py`, `runtime.py`, `runner.py`, `automation.py` |
| Cloud adapters | `feishu.py`, `mail_center.py`, `notion.py`, `email_delivery.py` |
| Search and stored briefs | `query.py`, `brief.py` |
| Optional local MCP | `mcp_server.py` and `tests/test_mcp.py` |

Keep Apple Mail access read-only and keep its undocumented schema isolated in
`apple_mail.py`. MCP must remain stdio-only and read-only unless a separately
reviewed product decision changes that boundary.

## Pull requests

1. Keep a PR scoped to one behavior or documentation change.
2. Add or update a meaningful synthetic test for behavior changes.
3. Run the relevant test command above and state the result in the PR.
4. Explain any new dependency, network flow, permission, stored data, or
   external service in `docs/privacy.md`, `docs/architecture.md`, and, for a
   durable design choice, `docs/design-rationale.md`.
5. Never include secrets or personal data. Use safe placeholders in examples.
6. Update `README.md` and `README.zh-CN.md` together when user-facing
   behavior changes.

## License of contributions

By submitting a pull request you agree that your contribution is licensed under
the project's [MIT License](LICENSE). Do not copy code from projects whose
license is incompatible with MIT.
