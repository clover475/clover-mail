# Privacy and permissions

## What stays on the Mac

Raw MIME, including original attachments, is stored in a local SQLite archive
under `~/Library/Application Support/CloverMail/`. The database and runtime
configuration are private files and are ignored by Git. The original Apple Mail
store is read-only. You can stop automation by unloading the per-user launchd
agent; removing a token stops future API calls but does not erase copies
already sent to a service.

## What leaves the Mac

| Destination | Data sent | Trigger |
| --- | --- | --- |
| Selected AI provider (MiMo, OpenAI-compatible API, or Anthropic) | One message's sender, subject, readable body and selected images; later, extracted facts for Brief. `ask` sends the question for search planning. | `analyze`, `ask`, `brief-generate`, or scheduled run. |
| Feishu | A row for **every archived message**, including readable original text (first 20,000 characters), AI title/summary/translation, actions, deadlines and status. Brief text goes to the configured private Open ID. | `feishu-publish`, `brief-send-feishu`, or scheduled run. |
| Notion (optional) | Complete extracted readable original text, translation, summary, actions and facts as database pages. No raw MIME or attachments. | `notion-publish` or scheduled run when configured. |
| SMTP (optional) | The generated Daily Brief, including facts and actions, to the configured recipient. | `brief-send-email` or scheduled run. |

Cloud mirrors contain sensitive correspondence. Restrict the Feishu Base and
Notion database to your own account or intended collaborators. Notion's
handling state is a mirror; edit that state in Feishu.

## macOS access

Apple Mail's local store is protected by macOS. Grant **Full Disk Access** only
to the executable that runs `clover-mail` (for a LaunchAgent, the Python
interpreter in its `ProgramArguments`). This grants broad filesystem access to
that process, not only Mail. Use a trusted local interpreter, inspect this
repository first, and revoke access in System Settings when no longer needed.
No Apple ID password or bypass is required.

## Images and tracking

Embedded and attached images are inspected and filtered on the Mac before any
selected image is sent to the chosen model provider. Remote images are not
requested by default. `CLOVER_MAIL_REMOTE_IMAGES=selective` considers at most
two likely informative HTTPS images per mail, rejects private addresses and
redirects, sends no cookies or Referer, and limits bytes and dimensions. A
unique remote URL can still tell its host that a message was viewed. Leave the
option disabled if that risk is unacceptable. The Feishu row and Brief flag
unloaded remote images as possible gaps.

## Model and prompt-injection boundary

The selected email text and chosen images leave the Mac when an AI operation
runs. HTTPS protects the connection in transit, but the selected provider can
process or retain the submitted content under its own account terms. Choose a
provider and endpoint you trust; do not use an arbitrary compatible endpoint.
The client rejects redirects so a provider cannot forward the request and API
key to a second host.

Email bodies and image text are untrusted input and can contain prompt
injection. The system prompt tells the model to treat them as data, and the
model has no tools or ability to execute commands. That reduces the impact but
does not make model output trustworthy: summaries, facts, and deadlines can be
wrong or manipulated. Verify important actions and dates against the original
message. API credentials are excluded from error messages and response bodies
are size-limited.

## Safe use and sharing

Never commit `runtime.env`, `mail.sqlite3`, `.eml`/`.emlx` files, logs, exported
briefs, or screenshots with real mail. The public artwork and tests use only
synthetic messages. Review cloud provider retention, workspace sharing, and
model pricing before enabling their adapters.
