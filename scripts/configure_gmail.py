"""One-time, local Gmail SMTP setup. Run interactively; never paste a password in chat."""

from __future__ import annotations

import getpass
import os
import re
import sys
import tempfile
from pathlib import Path

RUNTIME = Path.home() / ".config/clover-mail/runtime.env"


def _email(value: str, *, gmail: bool = False) -> str:
    value = value.strip()
    pattern = r"[^\s@]+@gmail\.com" if gmail else r"[^\s@]+@[^\s@]+\.[^\s@]+"
    if not re.fullmatch(pattern, value, flags=re.IGNORECASE):
        raise ValueError("请输入有效的邮箱地址" + ("（Gmail）" if gmail else ""))
    return value


def _write_settings(path: Path, settings: dict[str, str]) -> None:
    if not path.exists() or path.is_symlink():
        raise ValueError("私有运行配置不存在，或不是普通文件")
    lines = path.read_text().splitlines()
    keys = set(settings)
    lines = [line for line in lines if not any(line.startswith(key + "=") for key in keys)]
    lines.extend(f"{key}={value}" for key, value in settings.items())
    fd, temp_name = tempfile.mkstemp(prefix=".gmail-", dir=path.parent)
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
        print("请在本机交互式终端运行，密码不会显示。", file=sys.stderr)
        return 1
    try:
        sender = _email(input("Gmail 发件地址: "), gmail=True)
        recipient = _email(input(f"日报收件地址 [{sender}]: ") or sender)
        password = "".join(getpass.getpass("Gmail 应用专用密码: ").split())
        if len(password) != 16 or not password.isascii() or not password.isalnum():
            raise ValueError("应用专用密码应为 16 位字母或数字")
        _write_settings(RUNTIME, {
            "CLOVER_MAIL_SMTP_HOST": "smtp.gmail.com",
            "CLOVER_MAIL_SMTP_PORT": "465",
            "CLOVER_MAIL_SMTP_USER": sender,
            "CLOVER_MAIL_SMTP_PASSWORD": password,
            "CLOVER_MAIL_EMAIL_FROM": sender,
            "CLOVER_MAIL_EMAIL_TO": recipient,
        })
    except (OSError, ValueError) as exc:
        print(f"配置失败：{exc}", file=sys.stderr)
        return 1
    print("Gmail SMTP 已写入本机仅当前用户可读的配置；未显示密码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
