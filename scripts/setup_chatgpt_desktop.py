#!/usr/bin/env python3
"""Prepare a personal local ChatGPT plugin entry for Clover Mail."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PLUGIN_NAME = "clover-mail-local"
PLUGIN_MANIFEST = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": PLUGIN_NAME,
    "version": "0.2.0",
    "description": "Read-only access to your local Clover Mail archive.",
    "author": {"name": "Clover Mail"},
    "extensions": {
        "com.openai": {
            "interface": {
                "displayName": "Clover Mail Local",
                "shortDescription": "Search your local email archive",
                "capabilities": ["Read"],
                "developerName": "Clover Mail",
            }
        }
    },
}


class SetupError(Exception):
    """A safe, user-facing setup error without local paths or data."""


def _server_config(interpreter: str) -> dict:
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            PLUGIN_NAME: {
                "type": "stdio",
                "command": interpreter,
                "args": ["-m", "clover_mail.mcp_server"],
            }
        },
    }


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError("existing plugin configuration is unreadable or invalid JSON") from exc
    if not isinstance(value, dict):
        raise SetupError("existing plugin configuration has an invalid structure")
    return value


def _validate_existing_plugin(plugin_dir: Path, expected_plugin: dict, expected_mcp: dict) -> None:
    if not plugin_dir.exists():
        return
    if not plugin_dir.is_dir():
        raise SetupError("the Clover Mail plugin destination conflicts with an existing file")
    try:
        children = {child.name for child in plugin_dir.iterdir()}
    except OSError as exc:
        raise SetupError("the Clover Mail plugin destination is not accessible") from exc
    if not children:
        return
    if children != {"plugin.json", "mcp.json"}:
        raise SetupError("the Clover Mail plugin destination contains unrelated files")
    if _read_json(plugin_dir / "plugin.json") != expected_plugin or _read_json(plugin_dir / "mcp.json") != expected_mcp:
        raise SetupError("the existing Clover Mail plugin configuration differs; refusing to overwrite it")


def _marketplace(home: Path, expected_entry: dict) -> dict:
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    if not marketplace_path.exists():
        return {"name": "clover-mail-personal", "plugins": [expected_entry]}
    marketplace = _read_json(marketplace_path)
    plugins = marketplace.get("plugins")
    if not isinstance(marketplace.get("name"), str) or not isinstance(plugins, list):
        raise SetupError("existing marketplace configuration has an invalid structure")
    matches = [item for item in plugins if isinstance(item, dict) and item.get("name") == PLUGIN_NAME]
    if len(matches) > 1:
        raise SetupError("existing marketplace has duplicate Clover Mail entries")
    if matches:
        if matches[0] != expected_entry:
            raise SetupError("an existing marketplace entry conflicts with Clover Mail; refusing to overwrite it")
    else:
        plugins.append(expected_entry)
    return marketplace


def _atomic_json(path: Path, value: dict) -> None:
    temp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temp_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise SetupError("could not write plugin configuration") from exc


def install(home: Path | None = None) -> tuple[Path, Path]:
    home = (home or Path.home()).expanduser()
    # Keep venv launchers intact; resolving symlinks can point at the base Python
    # and lose the optional MCP dependency installed in the virtual environment.
    interpreter = str(Path(sys.executable).absolute())
    try:
        checked = subprocess.run(
            [interpreter, "-c", "import clover_mail.mcp_server; import mcp"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError("the current Python cannot load Clover Mail and the optional MCP SDK") from exc
    if checked.returncode:
        raise SetupError("the current Python cannot load Clover Mail and the optional MCP SDK")

    plugin_root = home / ".agents" / "plugins"
    plugin_dir = plugin_root / PLUGIN_NAME
    plugin_file = PLUGIN_MANIFEST
    mcp_file = _server_config(interpreter)
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./.agents/plugins/{PLUGIN_NAME}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }

    # Validate every existing destination before writing any of them.
    _validate_existing_plugin(plugin_dir, plugin_file, mcp_file)
    merged_marketplace = _marketplace(home, entry)

    _atomic_json(plugin_dir / "plugin.json", plugin_file)
    _atomic_json(plugin_dir / "mcp.json", mcp_file)
    marketplace_path = plugin_root / "marketplace.json"
    _atomic_json(marketplace_path, merged_marketplace)
    return plugin_dir, marketplace_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up Clover Mail for local ChatGPT desktop testing")
    parser.parse_args(argv)
    try:
        plugin_dir, marketplace_path = install()
    except SetupError as exc:
        print(f"ChatGPT plugin setup failed: {exc}", file=sys.stderr)
        return 1
    print("Local Clover Mail plugin configuration prepared.")
    print(f"Plugin: {plugin_dir}")
    print(f"Marketplace: {marketplace_path}")
    print("Restart ChatGPT desktop and add this personal marketplace/plugin in its UI if needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
