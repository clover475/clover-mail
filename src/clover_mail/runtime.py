"""Load private runtime settings and optional owner-only key files."""

from __future__ import annotations

import os
import stat
from pathlib import Path

ALLOWED = {
    "CLOVER_MAIL_AI_PROVIDER", "CLOVER_MAIL_AI_API_KEY", "CLOVER_MAIL_AI_BASE_URL",
    "CLOVER_MAIL_AI_MODEL", "CLOVER_MAIL_AI_MAX_TOKENS", "CLOVER_MAIL_AI_ENV_FILE",
    "MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "MIMO_MAX_TOKENS",
    "FEISHU_ENV_FILE", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID", "FEISHU_OPEN_ID",
    "CLOVER_MAIL_SMTP_HOST", "CLOVER_MAIL_SMTP_PORT", "CLOVER_MAIL_SMTP_USER",
    "CLOVER_MAIL_SMTP_PASSWORD", "CLOVER_MAIL_EMAIL_FROM", "CLOVER_MAIL_EMAIL_TO",
    "CLOVER_MAIL_REMOTE_IMAGES",
    "CLOVER_MAIL_TIMEZONE",
    "CLOVER_MAIL_NOTION_TOKEN", "CLOVER_MAIL_NOTION_DATA_SOURCE_ID",
}
SOURCE_KEY = "CLOVER_MAIL_MIMO_ENV_FILE"
GENERIC_SOURCE_KEY = "CLOVER_MAIL_AI_ENV_FILE"


def _owner_only_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"private runtime file does not exist: {path}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"private runtime file must have owner-only permissions: {path}")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        values[name.strip()] = value.strip().strip('"\'')
    return values


def load_runtime_env() -> None:
    selected = os.environ.get("CLOVER_MAIL_ENV_FILE")
    if not selected:
        return
    path = Path(selected).expanduser()
    values = _owner_only_values(path)
    source = values.get(GENERIC_SOURCE_KEY)
    if source:
        source_values = _owner_only_values(Path(source).expanduser())
        for key in ("CLOVER_MAIL_AI_PROVIDER", "CLOVER_MAIL_AI_API_KEY",
                    "CLOVER_MAIL_AI_BASE_URL", "CLOVER_MAIL_AI_MODEL",
                    "CLOVER_MAIL_AI_MAX_TOKENS"):
            if source_values.get(key):
                os.environ[key] = source_values[key]
        if not source_values.get("CLOVER_MAIL_AI_API_KEY"):
            raise ValueError("AI source file has no CLOVER_MAIL_AI_API_KEY")
    legacy_source = values.get(SOURCE_KEY)
    if legacy_source:
        source_values = _owner_only_values(Path(legacy_source).expanduser())
        for key in ("MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "MIMO_MAX_TOKENS"):
            if source_values.get(key):
                os.environ[key] = source_values[key]
        if not source_values.get("MIMO_API_KEY"):
            raise ValueError("legacy MiMo source file has no active MIMO_API_KEY")
    for key, value in values.items():
        if key in ALLOWED:
            os.environ[key] = value
