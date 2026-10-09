"""Load private runtime settings, with an optional pointer to an existing MiMo key file."""

from __future__ import annotations

import os
import stat
from pathlib import Path

ALLOWED = {
    "MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "MIMO_MAX_TOKENS",
    "FEISHU_ENV_FILE", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID", "FEISHU_OPEN_ID",
    "CLOVER_MAIL_SMTP_HOST", "CLOVER_MAIL_SMTP_PORT", "CLOVER_MAIL_SMTP_USER",
    "CLOVER_MAIL_SMTP_PASSWORD", "CLOVER_MAIL_EMAIL_FROM", "CLOVER_MAIL_EMAIL_TO",
    "CLOVER_MAIL_REMOTE_IMAGES",
    "CLOVER_MAIL_TIMEZONE",
    "CLOVER_MAIL_NOTION_TOKEN", "CLOVER_MAIL_NOTION_DATA_SOURCE_ID",
}
SOURCE_KEY = "CLOVER_MAIL_MIMO_ENV_FILE"


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
    source = values.get(SOURCE_KEY)
    if source:
        source_values = _owner_only_values(Path(source).expanduser())
        for key in ("MIMO_API_KEY", "MIMO_BASE_URL"):
            if source_values.get(key):
                os.environ[key] = source_values[key]
        if not source_values.get("MIMO_API_KEY"):
            raise ValueError("MiMo source file has no active MIMO_API_KEY")
    for key, value in values.items():
        if key in ALLOWED:
            os.environ[key] = value
