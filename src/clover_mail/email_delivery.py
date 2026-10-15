"""Send the generated brief through an explicitly configured SMTP relay."""

from __future__ import annotations

import os
import hashlib
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage


class DeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str

    @classmethod
    def from_environment(cls) -> "SMTPConfig":
        names = ("CLOVER_MAIL_SMTP_HOST", "CLOVER_MAIL_SMTP_USER", "CLOVER_MAIL_SMTP_PASSWORD",
                 "CLOVER_MAIL_EMAIL_FROM", "CLOVER_MAIL_EMAIL_TO")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise DeliveryError("missing email configuration: " + ", ".join(missing))
        try:
            port = int(os.environ.get("CLOVER_MAIL_SMTP_PORT", "465"))
        except ValueError as exc:
            raise DeliveryError("CLOVER_MAIL_SMTP_PORT must be a number") from exc
        if not 1 <= port <= 65535:
            raise DeliveryError("invalid SMTP port")
        return cls(os.environ[names[0]], port, os.environ[names[1]], os.environ[names[2]],
                   os.environ[names[3]], os.environ[names[4]])


def send_brief(*, day: str, body: str, config: SMTPConfig) -> None:
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = config.recipient
    message["Subject"] = f"Clover Mail 日报 · {day}"
    # An updated evening brief must have a different ID from an early validation copy.
    # Retrying the same content keeps the same ID.
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    message["Message-ID"] = f"<clover-mail-brief-{day.replace('-', '')}-{digest}@local.clover-mail>"
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL(config.host, config.port, context=ssl.create_default_context(), timeout=30) as server:
            server.login(config.username, config.password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"email delivery failed: {type(exc).__name__}") from None
