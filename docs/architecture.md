# Architecture and design choices

## Data path

1. `apple_mail.py` opens the local `Envelope Index` in SQLite read-only mode,
   checks known table names, and reads cached `.emlx` files. The source is
   never mutated. If Mail has not downloaded a message, Mail Memory cannot
   extract its body.
2. `archive.py` stores raw MIME, a stable per-account identity, processing
   checkpoints, analysis, delivery state, and search text in a local SQLite
   database under `~/Library/Application Support/CloverMail/`.
3. `processing.py` extracts readable text and bounded images. `ai.py` sends
   one message's necessary content at a time to the configured MiMo,
   OpenAI-compatible, or Anthropic endpoint and validates structured results.
   Long text uses bounded chunks so the translation can remain full.
4. `mail_center.py` creates and updates Feishu Base rows. All archived mail is
   represented, while pending AI work is explicitly marked. Feishu is the
   authority for the handling state, which `pull_statuses` copies locally.
5. `brief.py` synthesizes already extracted facts into a Chinese daily brief.
   `runner.py` sends it to a private Feishu recipient and SMTP email, with
   local delivery checkpoints. `automation.py` installs a per-user launchd
   agent after a real manual validation.
6. `query.py` asks the selected provider to interpret a question, retrieves candidates locally,
   and returns cited matching records. `notion.py` is an optional one-way text
   mirror; it is not the handling-state authority.

## Why this shape

- Python standard-library runtime keeps installation and maintenance small.
- Local SQLite preserves the original source so future model versions can
  reanalyze messages without asking a cloud system to store the mailbox.
- Feishu and Notion adapters are downstream mirrors. Their outages do not
  change the Apple Mail source.
- The Apple Mail index is private, undocumented data. Its schema checks and
  account heuristics are isolated in one file, where macOS changes can be
  handled without changing the archive or AI pipeline.

## Deliberate omissions

No automatic replies or Mail writes; no cloud mailbox import; no heavy RAG
framework; no topic taxonomy; no Feishu chat query bot yet. The CLI query and
SQLite archive keep that extension possible without claiming it exists now.
