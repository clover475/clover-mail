"""Configure one AI provider locally without echoing its API key."""

from __future__ import annotations

import getpass
import os
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

RUNTIME = Path.home() / ".config/clover-mail/runtime.env"
PROVIDERS = {
    "mimo": "https://api.xiaomimimo.com/v1",
    "openai-compatible": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}


def _validate_endpoint(value: str) -> str:
    parts = urlsplit(value.strip())
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment):
        raise ValueError("API endpoint must use HTTPS and contain no credentials, query, or fragment")
    return value.strip().rstrip("/")


def _write_settings(path: Path, settings: dict[str, str]) -> None:
    if path.is_symlink():
        raise ValueError("runtime configuration must not be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.exists():
        if not path.is_file():
            raise ValueError("runtime configuration must be a regular file")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("runtime configuration is accessible to other users; run chmod 600 first")
        lines = path.read_text().splitlines()
    else:
        lines = []
    keys = set(settings)
    lines = [line for line in lines if not any(line.startswith(key + "=") for key in keys)]
    lines.extend(f"{key}={value}" for key, value in settings.items())
    fd, temp_name = tempfile.mkstemp(prefix=".ai-", dir=path.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(lines) + "\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> int:
    if not sys.stdin.isatty():
        print("Run this command in a local interactive terminal; the API key will stay hidden.", file=sys.stderr)
        return 1
    try:
        provider = input("Provider (mimo / openai-compatible / anthropic): ").strip().lower()
        if provider not in PROVIDERS:
            raise ValueError("Choose mimo, openai-compatible, or anthropic")
        endpoint = _validate_endpoint(input(f"HTTPS API endpoint [{PROVIDERS[provider]}]: ").strip()
                                      or PROVIDERS[provider])
        model = input("Model ID (from the provider console): ").strip()
        if not model or "\n" in model or "\r" in model:
            raise ValueError("Model ID is required")
        api_key = getpass.getpass("API key (hidden): ").strip()
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise ValueError("API key is required")
        _write_settings(RUNTIME, {
            "CLOVER_MAIL_AI_PROVIDER": provider,
            "CLOVER_MAIL_AI_BASE_URL": endpoint,
            "CLOVER_MAIL_AI_MODEL": model,
            "CLOVER_MAIL_AI_API_KEY": api_key,
        })
    except (OSError, ValueError) as exc:
        print(f"Configuration failed: {exc}", file=sys.stderr)
        return 1
    print("AI provider saved in ~/.config/clover-mail/runtime.env (owner-only); API key was not displayed.")
    print("Ensure CLOVER_MAIL_ENV_FILE points to this runtime.env before running Clover Mail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
