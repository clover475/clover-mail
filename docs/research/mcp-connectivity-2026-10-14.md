# MCP connectivity research — 2026-10-14

## Question

How can Clover Mail's local, stdio-only MCP server be used by desktop clients
and ChatGPT without making unsupported connectivity claims?

## Sources reviewed

- [OpenAI Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), accessed 2026-10-14.
- [OpenAI Help: Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt), accessed 2026-10-14.
- [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk), accessed 2026-10-14.
- [MCP Python SDK: connect to a real host](https://py.sdk.modelcontextprotocol.io/get-started/real-host/), accessed 2026-10-14.
- [OpenAI: connect and test your plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt), accessed 2026-10-14.
- [OpenAI: submit desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only), accessed 2026-10-14.
- [OpenAI: package your plugin](https://developers.openai.com/plugins/build/plugins), accessed 2026-10-14.

## Official findings

| Finding | Evidence | Product implication |
| --- | --- | --- |
| Secure MCP Tunnel connects private MCP servers to supported OpenAI products through an outbound HTTPS connection. Its client can reach a configured stdio or HTTP server. | OpenAI tunnel guide | A local stdio server can be a tunnel target without adding an inbound public port. |
| Tunnel setup needs a tunnel ID, runtime API key, and relevant Platform permissions. A target ChatGPT workspace must be associated; ChatGPT developer mode is separately controlled by workspace policy. | OpenAI tunnel guide; ChatGPT developer-mode help | Tunnel setup is an opt-in external integration, not part of a no-credential local installation. |
| ChatGPT developer-mode users add an app endpoint, scan its tools, and create the app; workspace roles and plan policy control who can enable or publish it. | ChatGPT developer-mode help | Documentation must direct users to their administrator where required and must not imply universal availability. |
| The official Python SDK supports servers and clients over stdio, Streamable HTTP, and SSE. The repository identifies it as the official SDK and has an MIT license. | MCP Python SDK | The optional `mcp>=2,<3` dependency is a suitable, separately installable transport implementation. |
| The SDK's real-host guide shows an `mcpServers` JSON entry for Claude Desktop's configuration. | MCP Python SDK real-host guide | Claude Desktop documentation can use the standard local stdio configuration shape with an absolute executable path. |
| OpenAI documents desktop-only plugins for local stdio MCP servers. The published-submission path requires an approved partner allowlist and packages a stdio launch command in `mcp.json`. | OpenAI desktop-only plugin guide; package guide | A personal local marketplace package is a practical local-test path, but Clover Mail must not claim public submission or tested ChatGPT UI support. |

## Verified in this repository

- `mcp_server.py` implements six tools over **stdio only** and opens the local
  SQLite archive in read-only mode.
- Synthetic automated tests exercise SDK v2 stdio discovery and a `mail_search`
  call. The implementation owner reported 19 MCP tests and 85 full-suite tests
  passing on 2026-10-14 in Python 3.11 and Python 3.14 virtual environments.
- The personal local-marketplace generator and its exact generated stdio command
  were exercised in isolated-home tests with synthetic data. Those tests did
  not touch the user's real ChatGPT configuration.
- The MCP path requires an already-created archive. It does not require model
  provider configuration or Feishu setup.
- Tool output marks email data as untrusted. Body and cached translation are
  opt-in and bounded; raw MIME is not returned.

## Not verified / do not claim

- No Claude Desktop or Codex graphical client configuration was run against a
  real mail archive in this delivery.
- ChatGPT Desktop UI installation, @mention, and a live tool call through the
  generated personal local-marketplace package were not exercised.
- No Secure MCP Tunnel was created, no OpenAI API key was supplied, and no
  ChatGPT workspace, developer-mode app, or live ChatGPT tool call was tested.
- No public HTTP MCP endpoint or plugin submission is included. A tunnel is
  private connectivity, not evidence that this project is published as a
  public ChatGPT integration.

## Decision

Make the generated personal ChatGPT Desktop local plugin the recommended
first client-test path, while labeling its UI flow unverified. Keep Codex and
Claude configuration examples as alternatives. Document Secure MCP Tunnel as
an optional, separately configured cloud path with its permissions and API-key
boundary. Keep all claims about the implemented product limited to its tested
local stdio server.

## Update — 2026-10-15

Codex (`codex mcp add`, CLI bundled with ChatGPT Desktop) and Claude Code
(`claude mcp add -s user`) each called four of the six tools end to end against
a real local archive. The Codex run exposed that unannotated tools are refused
under a `never` approval policy; all tools now declare `readOnlyHint`. The
ChatGPT Desktop plugin UI, Claude Desktop and Secure MCP Tunnel remain
unverified.
