# Mail Memory project status

Updated: 2026-10-09

## Current state

- Public MIT release: `https://github.com/clover475/clover-mail`.
- Local-first archive and Apple Mail read-only ingestion are implemented.
- AI configuration supports MiMo, OpenAI-compatible Chat Completions, and
  Anthropic Messages. Provider/model cache keys are kept distinct.
- A local hidden-prompt setup script writes provider credentials to a private
  runtime file without echoing the key.
- Feishu, optional Notion, SMTP brief, local search, and launchd adapters are
  implemented; live account setup and scheduling remain user-configured.

## Security posture

- Public fixtures are synthetic. Current tracked-file scans found no
  API-key-like values or local `/Users/...` paths.
- GitHub secret scanning and push protection are enabled. Dependabot alerts and
  security updates were enabled during the 2026-10-09 security review.
- Provider endpoints require HTTPS, redirects are rejected, response bodies
  are size-limited, and error text does not include provider response bodies.
- Mail and image text are untrusted input. Prompt injection remains a residual
  risk; the model has no tools, and users must verify important results.
- External AI, Feishu, Notion, remote image hosts, and SMTP receive only the
  content described in `docs/privacy.md` when the matching feature runs.

## Validation

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`
- Latest run: 66 tests passed on 2026-10-09.

## Next work

Validate the chosen AI provider and Feishu workspace with one low-risk message,
then configure Daily Brief delivery and scheduling only after the manual path
works end to end.
