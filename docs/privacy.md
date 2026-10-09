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
| MiMo | One message's sender, subject, readable body and selected images; later, extracted facts for Brief. `ask` sends the question for search planning. | `analyze`, `ask`, `brief-generate`, or scheduled run. |
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

Embedded and attached images can be analyzed locally. Remote images are not
requested by default. `CLOVER_MAIL_REMOTE_IMAGES=selective` considers at most
two likely informative HTTPS images per mail, rejects private addresses and
redirects, sends no cookies or Referer, and limits bytes and dimensions. A
unique remote URL can still tell its host that a message was viewed. Leave the
option disabled if that risk is unacceptable. The Feishu row and Brief flag
unloaded remote images as possible gaps.

## Safe use and sharing

Never commit `runtime.env`, `mail.sqlite3`, `.eml`/`.emlx` files, logs, exported
briefs, or screenshots with real mail. The public artwork and tests use only
synthetic messages. Review cloud provider retention, workspace sharing, and
model pricing before enabling their adapters.
