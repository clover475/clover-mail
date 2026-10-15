#!/usr/bin/env python3
"""Safely verify the local MCP stdio server against the installed archive."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

EXPECTED_TOOLS = {
    "mail_list_accounts", "mail_get_summary", "mail_search",
    "mail_get_message", "mail_get_pending_actions", "mail_get_daily_brief",
}


def _payload(result) -> dict:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    raise RuntimeError("tool returned no structured result")


async def _check(database: Path | None) -> tuple[int, int, int]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    args = ["-m", "clover_mail.mcp_server"]
    if database is not None:
        args.extend(["--database", str(database.expanduser())])
    params = StdioServerParameters(command=sys.executable, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            if names != EXPECTED_TOOLS:
                raise RuntimeError("unexpected tool list")
            result = _payload(await session.call_tool("mail_list_accounts", {}))
            if result.get("status") != "ok" and "accounts" not in result:
                raise RuntimeError("archive unavailable")
            accounts = result.get("accounts")
            if not isinstance(accounts, list):
                raise RuntimeError("invalid account summary")
            count = 0
            for account in accounts:
                message_count = account.get("message_count") if isinstance(account, dict) else None
                if isinstance(message_count, bool) or not isinstance(message_count, int) or message_count < 0:
                    raise RuntimeError("invalid account summary")
                count += message_count
            return len(names), len(accounts), count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the local Clover Mail MCP connection safely")
    parser.add_argument("--database", type=Path, default=None,
                        help="optional existing archive path (defaults to the Clover Mail archive)")
    args = parser.parse_args(argv)
    try:
        tool_count, account_count, message_count = asyncio.run(_check(args.database))
    except ImportError:
        print("MCP preflight failed: install optional support with pip install 'clover-mail[mcp]'", file=sys.stderr)
        return 1
    except Exception:
        # The SDK, SQLite, and user configuration errors can contain local paths.
        print("MCP preflight failed: unable to connect to or read the local archive", file=sys.stderr)
        return 1
    print(f"MCP preflight passed: tools={tool_count}; accounts={account_count}; messages={message_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
