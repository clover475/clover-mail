# Design rationale

## Separate public history

**Decision:** release a clean source tree with a new Git history, rather than
publish the working personal repository.

**Why:** the private history was written during live setup and includes local
paths, operational notes, and commit metadata that should not become a public
artifact. The release tree contains only reusable code, synthetic tests,
general documentation, and artwork made from fictional messages.

**Trade-off:** public contributors cannot inspect the original development
history. Future changes can be mirrored into the public tree after a privacy
review. A clean release does not erase already shared cloud mail copies.

**How to explain it:** a production-minded open-source release needs a data
boundary and a publication boundary; Git history is part of the data surface.

**Known weakness:** keeping private and public trees aligned takes deliberate
maintenance until development moves to one sanitized source branch.

## Keep the raw archive local

**Decision:** store original MIME and sync state in local SQLite, and send
bounded per-message content to the selected AI provider while treating Feishu
and optional Notion as downstream copies.

**Why:** the user can reprocess old mail when prompts or models improve, and a
remote service outage does not lose the source record.

**Trade-off:** the Mac must be available for scheduled work; Feishu and Notion
still hold sensitive text when enabled.

**How to explain it:** the architecture separates durable source data from
derived AI fields and cloud presentation.

**Known weakness:** no built-in encrypted backup or multi-device sync for the
local database. Users should protect the Mac with FileVault and their normal
backup policy.

## Keep model providers replaceable

**Decision:** use a small provider adapter for MiMo, OpenAI-compatible chat
completions, and Anthropic Messages rather than binding the archive to one
vendor.

**Why:** users can choose a model based on quality, cost, and data handling;
provider-specific cache keys prevent one model's result from being mistaken
for another's.

**Trade-off:** compatible APIs do not guarantee identical features or image
support. The configured endpoint must be trusted because email content is sent
to it.

**Security boundary:** require HTTPS, reject redirects, cap response size, and
do not expose tools to email content. Email remains untrusted input, so prompt
injection and incorrect model output remain possible.

## Adapt to Apple Mail's local cache

**Decision:** use read-only access to the local Envelope Index and `.emlx`
files, with schema checks in one module.

**Why:** Apple Mail already aggregates accounts without asking this tool for
each provider's credentials. A local connector keeps the first release small.

**Trade-off:** Apple's schema is undocumented and only locally cached bodies
can be read. New macOS versions may need changes.

**How to explain it:** this is a consciously narrow adapter, with an explicit
failure path when Mail's storage differs.

**Known weakness:** source-account names are inferred from mailbox metadata
and can be wrong. The original account ID remains in the local archive.

## Add a local, read-only MCP boundary

**Decision:** expose the existing archive through a small stdio-only MCP
server with six query tools, built on the official MCP Python SDK as an
optional dependency.

**Why:** a desktop assistant can answer questions from the user's already
synced archive without receiving the whole mailbox from a new cloud pipeline.
The MCP server opens SQLite in read-only mode and deliberately offers no
write, send, sync, analysis, or Brief-generation action. Keeping the SDK
optional preserves the base install for people who only use the CLI.

**Trade-off:** local stdio needs a compatible client running on the Mac and
does not make the archive reachable from an ordinary cloud chat by itself. The
archive must already exist, data can be stale until the normal sync runs, and
an MCP client still sees any mail text it asks the tool to return. The optional
personal ChatGPT Desktop plugin package preserves the local launch boundary,
but its tool results can still reach the ChatGPT model/service.

**How to explain it:** this is a capability boundary: the assistant can query
prepared local data, while the mail pipeline remains responsible for ingest,
model analysis, and cloud mirrors. Read-only tools make the first integration
auditable and limit the blast radius of prompt-injected email content.

**Known weakness:** account identifiers can be opaque, while unread state is
not stored. Users can make a local, explicit alias map, but the server never
guesses an email address or provider type. Tool responses label email content
as untrusted data; that label reduces ambiguity but cannot prevent a client or
model from mishandling malicious text. The generated ChatGPT Desktop plugin is
not installed or enabled automatically, and its UI path has not yet been
verified against a real account.
