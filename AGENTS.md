# AGENTS.md

Guidance for AI coding agents working in this repository. Human contributors: see [CONTRIBUTING.md](CONTRIBUTING.md), which this file does not repeat.

## Check

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

Setup is in CONTRIBUTING.md. CI runs the same suite on macOS and Ubuntu, Python 3.11 and 3.13. Run it before saying a change works, and state the result.

## Boundaries that are easy to break

- **Synthetic data only.** Never read, copy, or point a test or script at a real mailbox, `.emlx` file, or SQLite archive. Fixtures under `tests/` are synthetic.
- **Apple Mail access stays read-only**, and its undocumented schema stays isolated in `apple_mail.py`.
- **The MCP server stays stdio-only and read-only** unless a reviewed product decision says otherwise.
- **No secrets or personal data** in code, tests, docs, commit messages, or examples. Use placeholders.
- A new dependency, network flow, permission, stored data, or external service must be documented in `docs/privacy.md` and `docs/architecture.md` (and `docs/design-rationale.md` for durable choices).

Module map and PR rules: CONTRIBUTING.md.
