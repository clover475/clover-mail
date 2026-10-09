"""Read Apple Mail's local index and cached EMLX messages without mutation."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import unquote, quote

MAIL_ROOT = Path.home() / "Library" / "Mail"
APPLE_EPOCH_OFFSET = 978_307_200


class MailSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceMessage:
    account_id: str
    mailbox: str
    rowid: int
    source_date: int | float | str | None
    raw_message: bytes


def discover_mail_root(mail_root: Path = MAIL_ROOT) -> Path:
    if not mail_root.exists():
        raise MailSourceError(f"Apple Mail data folder not found: {mail_root}")
    try:
        versions = [p for p in mail_root.iterdir() if re.fullmatch(r"V\d+", p.name)]
    except PermissionError as exc:
        raise MailSourceError(
            f"macOS denied access to {mail_root}; grant Full Disk Access to the app running Mail Memory."
        ) from exc
    if not versions:
        raise MailSourceError(
            f"No local Apple Mail index found under {mail_root}; open Mail and allow it to finish syncing accounts."
        )
    return max(versions, key=lambda p: int(p.name[1:]))


def _open_index(index: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(index))}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT 1 FROM mailboxes LIMIT 1").fetchone()
        return connection
    except sqlite3.Error as exc:
        raise MailSourceError(
            f"Cannot read Apple Mail index ({exc}). The application running Mail Memory may need Full Disk Access."
        ) from exc


def doctor(mail_root: Path = MAIL_ROOT) -> dict[str, object]:
    root = discover_mail_root(mail_root)
    index = root / "MailData" / "Envelope Index"
    if not index.is_file():
        raise MailSourceError(f"Apple Mail index not found at {index}")
    db = _open_index(index)
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"messages", "mailboxes", "subjects", "addresses"}
        missing = required - tables
        if missing:
            raise MailSourceError(
                "Apple Mail index schema is not recognized; missing tables: " + ", ".join(sorted(missing))
            )
        account_count = db.execute(
            "SELECT COUNT(DISTINCT substr(url, instr(url, '://') + 3, instr(substr(url, instr(url, '://') + 3), '/') - 1)) FROM mailboxes"
        ).fetchone()[0]
        message_count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        mailbox_count = db.execute("SELECT COUNT(*) FROM mailboxes").fetchone()[0]
    finally:
        db.close()
    return {
        "mail_root": str(root),
        "index": str(index),
        "accounts": int(account_count or 0),
        "mailboxes": int(mailbox_count or 0),
        "indexed_messages": int(message_count or 0),
        "read_only": True,
    }


def account_labels(mail_root: Path = MAIL_ROOT) -> dict[str, str]:
    """Identify source accounts from Mail's read-only mailbox URLs, not senders."""
    root = discover_mail_root(mail_root)
    db = _open_index(root / "MailData" / "Envelope Index")
    groups: dict[str, list[tuple[str, str]]] = {}
    try:
        for (url,) in db.execute("SELECT url FROM mailboxes"):
            source = _account_from_url(url)
            if source:
                account_id, mailbox = source
                groups.setdefault(account_id, []).append((url.split(":", 1)[0].lower(), mailbox))
    finally:
        db.close()
    labels: dict[str, str] = {}
    for account_id, mailboxes in groups.items():
        if any(protocol == "ews" for protocol, _ in mailboxes):
            labels[account_id] = "Outlook"
        elif any("[gmail]" in mailbox.casefold() for _, mailbox in mailboxes):
            labels[account_id] = "Gmail"
        elif any("163" in mailbox.casefold() for _, mailbox in mailboxes):
            labels[account_id] = "163 Mail"
        else:
            labels[account_id] = "Other"
    return labels


def _emlx_raw(path: Path) -> bytes:
    data = path.read_bytes()
    newline = data.find(b"\n")
    if newline < 0:
        raise ValueError("malformed EMLX envelope")
    try:
        body_length = int(data[:newline].strip())
    except ValueError as exc:
        raise ValueError("invalid EMLX byte count") from exc
    start = newline + 1
    raw = data[start : start + body_length]
    if body_length <= 0 or len(raw) != body_length or b"\n" not in raw:
        raise ValueError("incomplete EMLX message body")
    # Validate that the extracted content is parseable RFC822 before archiving.
    BytesParser(policy=policy.default).parsebytes(raw)
    return raw


def _account_from_url(url: str) -> tuple[str, str] | None:
    match = re.match(r"^(?:imap|ews|pop|local)://([^/]+)/(.+)$", url)
    if not match:
        return None
    return match.group(1), unquote(match.group(2))


def _relative_mail_dirs(account_dir: Path) -> list[Path]:
    # Apple Mail stores messages in one or more mailbox store UUID folders.
    # Enumerating Data directories once per account avoids a whole-tree scan per message.
    try:
        return [path for path in account_dir.rglob("Data") if path.is_dir()]
    except OSError:
        return []


def _emlx_path(data_dirs: list[Path], rowid: int) -> Path | None:
    digits: list[str] = []
    bucket = rowid // 1000
    while bucket:
        digits.append(str(bucket % 10))
        bucket //= 10
    for data_dir in data_dirs:
        messages_dir = data_dir.joinpath(*digits, "Messages")
        for suffix in (".emlx", ".partial.emlx"):
            candidate = messages_dir / f"{rowid}{suffix}"
            if candidate.is_file():
                return candidate
    # A narrow fallback handles store layouts that differ between Mail versions.
    for data_dir in data_dirs:
        for name in (f"{rowid}.emlx", f"{rowid}.partial.emlx"):
            try:
                found = next(data_dir.parent.rglob(name), None)
            except OSError:
                found = None
            if found:
                return found
    return None


def _source_cutoff(since: datetime, latest_source_date: float) -> int:
    # Detect the index clock from its latest message. The old conservative
    # cutoff selected almost the entire Unix-timestamp index on every run.
    # RFC822 Date remains authoritative after the local body is read.
    if latest_source_date > 100_000_000_000:
        return int(since.timestamp() * 1000)
    if latest_source_date < 1_300_000_000:
        return int(since.timestamp()) - APPLE_EPOCH_OFFSET
    return int(since.timestamp())


def iter_messages(
    root: Path,
    *,
    since: datetime,
) -> tuple[list[SourceMessage], list[str]]:
    """Return complete messages from every local mailbox newer than `since`.

    Individual source failures are returned without including email content.
    """
    index = root / "MailData" / "Envelope Index"
    db = _open_index(index)
    messages: list[SourceMessage] = []
    errors: list[str] = []
    data_dirs: dict[str, list[Path]] = {}
    try:
        # Use published table/column names seen in Apple Mail's index. We fail closed
        # rather than guessing when a major schema change is detected.
        columns = {
            table: {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for table in ("messages", "mailboxes", "subjects", "addresses")
        }
        expected = {
            "messages": {"mailbox", "date_received"},
            "mailboxes": {"url"},
            "subjects": {"subject"},
            "addresses": {"address"},
        }
        for table, required in expected.items():
            if not required <= columns[table]:
                raise MailSourceError(
                    f"Apple Mail schema changed: {table} is missing " + ", ".join(sorted(required - columns[table]))
                )
        latest = db.execute("SELECT MAX(CAST(date_received AS REAL)) FROM messages").fetchone()[0]
        if latest is None:
            return messages, errors
        cutoff = _source_cutoff(since, float(latest))
        # Lower-bound the index in its detected epoch, then check RFC822 Date.
        rows = db.execute(
            """SELECT m.ROWID, m.date_received, mb.url
               FROM messages AS m JOIN mailboxes AS mb ON m.mailbox = mb.ROWID
               WHERE m.date_received >= ?
               ORDER BY m.date_received ASC""",
            (cutoff,),
        )
        for rowid, source_date, url in rows:
            parsed_url = _account_from_url(url or "")
            if not parsed_url:
                errors.append(f"message {rowid}: unsupported mailbox URL")
                continue
            account_id, mailbox = parsed_url
            if mailbox.rstrip("/").rsplit("/", 1)[-1].casefold() in {
                "sent", "sent messages", "sent mail", "drafts", "trash", "deleted messages",
                "junk", "junk e-mail", "spam", "outbox",
            }:
                continue
            account_dir = root / account_id
            if account_id not in data_dirs:
                data_dirs[account_id] = _relative_mail_dirs(account_dir)
            path = _emlx_path(data_dirs[account_id], int(rowid))
            if path is None:
                errors.append(f"message {rowid}: local body is not cached")
                continue
            try:
                raw = _emlx_raw(path)
                parsed = BytesParser(policy=policy.default).parsebytes(raw)
                date_header = parsed.get("Date")
                if date_header:
                    parsed_date = parsedate_utc(str(date_header))
                    if parsed_date and parsed_date < since:
                        continue
                elif source_date is not None:
                    # Use the source index date only when the RFC822 Date header is absent.
                    try:
                        numeric_date = float(source_date)
                        if numeric_date > 100_000_000_000:
                            numeric_date /= 1000
                        if numeric_date < 1_300_000_000:
                            numeric_date += APPLE_EPOCH_OFFSET
                        if numeric_date < since.timestamp():
                            continue
                    except (TypeError, ValueError, OverflowError):
                        pass
                messages.append(
                    SourceMessage(
                        account_id=account_id,
                        mailbox=mailbox,
                        rowid=int(rowid),
                        source_date=source_date,
                        raw_message=raw,
                    )
                )
            except (OSError, ValueError) as exc:
                errors.append(f"message {rowid}: unreadable local body ({exc})")
    finally:
        db.close()
    return messages, errors


def parsedate_utc(value: str) -> datetime | None:
    from email.utils import parsedate_to_datetime

    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)
