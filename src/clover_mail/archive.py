"""Private local message archive and sync checkpoint."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .content import extract_content
from .apple_mail import parsedate_utc
from .timezone import timezone_name as configured_timezone_name

DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "CloverMail"


def _derived_text(result: dict) -> str:
    return "\n".join([
        str(result.get("title_zh", "")), str(result.get("summary_zh", "")),
        str(result.get("translation_zh", "")),
        *[str(item.get("action", "")) for item in result.get("action_items", [])],
        *[str(item) for item in result.get("important_facts", [])],
    ])


class Archive:
    def __init__(self, path: Path | None = None) -> None:
        self.directory = (path or DEFAULT_DATA_DIR).expanduser()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        self.path = self.directory / "mail.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(
            """CREATE TABLE IF NOT EXISTS messages (
                   id INTEGER PRIMARY KEY,
                   identity_key TEXT NOT NULL UNIQUE,
                   message_id TEXT,
                   account_id TEXT NOT NULL,
                   mailbox TEXT NOT NULL,
                   apple_rowid INTEGER NOT NULL,
                   sender TEXT NOT NULL,
                   recipients TEXT NOT NULL,
                   subject TEXT NOT NULL,
                   date_header TEXT,
                   source_date TEXT,
                   raw_message BLOB NOT NULL,
                   content_sha256 TEXT NOT NULL,
                   imported_at TEXT NOT NULL
               );
               CREATE TABLE IF NOT EXISTS sync_state (
                   id INTEGER PRIMARY KEY CHECK (id = 1),
                   last_success_at TEXT NOT NULL
               );
               CREATE TABLE IF NOT EXISTS sync_runs (
                   id INTEGER PRIMARY KEY,
                   started_at TEXT NOT NULL,
                   finished_at TEXT NOT NULL,
                   imported INTEGER NOT NULL,
                   skipped INTEGER NOT NULL,
                   errors INTEGER NOT NULL
               );
               CREATE TABLE IF NOT EXISTS analyses (
                   id INTEGER PRIMARY KEY,
                   message_id INTEGER NOT NULL REFERENCES messages(id),
                   model TEXT NOT NULL,
                   prompt_version INTEGER NOT NULL,
                   result_json TEXT NOT NULL,
                   selected_images INTEGER NOT NULL,
                   processed_at TEXT NOT NULL,
                   UNIQUE(message_id, model, prompt_version)
               );
               CREATE TABLE IF NOT EXISTS analysis_chunks (
                   message_id INTEGER NOT NULL REFERENCES messages(id),
                   model TEXT NOT NULL,
                   prompt_version INTEGER NOT NULL,
                   chunk_index INTEGER NOT NULL,
                   chunk_sha256 TEXT NOT NULL,
                   result_json TEXT NOT NULL,
                   PRIMARY KEY(message_id, model, prompt_version, chunk_index)
               );
               CREATE TABLE IF NOT EXISTS analysis_failures (
                   message_id INTEGER NOT NULL REFERENCES messages(id),
                   model TEXT NOT NULL,
                   prompt_version INTEGER NOT NULL,
                   attempts INTEGER NOT NULL,
                   retry_after TEXT NOT NULL,
                   PRIMARY KEY(message_id, model, prompt_version)
               );
               CREATE TABLE IF NOT EXISTS handling (
                   message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                   status TEXT NOT NULL CHECK (status IN ('未处理', '处理中', '已完成', '忽略')),
                   updated_at TEXT NOT NULL
               );
               CREATE TABLE IF NOT EXISTS feishu_records (
                   message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                   record_id TEXT NOT NULL UNIQUE,
                   published_at TEXT NOT NULL,
                   synced_analysis_id INTEGER,
                   schema_version INTEGER NOT NULL DEFAULT 1
               );
               CREATE TABLE IF NOT EXISTS notion_records (
                   message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                   page_id TEXT NOT NULL UNIQUE,
                   published_at TEXT NOT NULL,
                   synced_analysis_id INTEGER,
                   schema_version INTEGER NOT NULL DEFAULT 1
               );
               CREATE TABLE IF NOT EXISTS daily_briefs (
                   day TEXT PRIMARY KEY,
                   body TEXT NOT NULL,
                   model TEXT NOT NULL,
                   usage_json TEXT NOT NULL,
                   generated_at TEXT NOT NULL,
                   feishu_sent_at TEXT,
                   email_sent_at TEXT
               );"""
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(feishu_records)")}
        if "synced_analysis_id" not in columns:
            self.db.execute("ALTER TABLE feishu_records ADD COLUMN synced_analysis_id INTEGER")
        if "schema_version" not in columns:
            self.db.execute("ALTER TABLE feishu_records ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")
        self.db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(title, sender, body, derived, tokenize='trigram')"
        )
        for row in self.db.execute(
            """SELECT m.id, m.subject, m.sender, m.raw_message,
                      (SELECT result_json FROM analyses WHERE message_id=m.id ORDER BY id DESC LIMIT 1)
               FROM messages AS m
               LEFT JOIN search_fts AS f ON f.rowid=m.id WHERE f.rowid IS NULL"""
        ).fetchall():
            derived = _derived_text(json.loads(row[4])) if row[4] else ""
            self.db.execute(
                "INSERT INTO search_fts(rowid, title, sender, body, derived) VALUES (?, ?, ?, ?, ?)",
                (row[0], row[1], row[2], extract_content(row[3]).text, derived),
            )
        self.db.commit()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def latest_success(self) -> datetime | None:
        row = self.db.execute("SELECT last_success_at FROM sync_state WHERE id=1").fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row[0]).astimezone(timezone.utc)

    def add(self, source) -> bool:
        parsed = BytesParser(policy=policy.default).parsebytes(source.raw_message)
        message_id = str(parsed.get("Message-ID", "")).strip() or None
        if message_id:
            identity = f"{source.account_id}:mid:{message_id.casefold()}"
        else:
            identity = f"{source.account_id}:row:{source.rowid}"
        recipients = ", ".join(str(parsed.get_all("To", [])))
        digest = hashlib.sha256(source.raw_message).hexdigest()
        values = (
            identity,
            message_id,
            source.account_id,
            source.mailbox,
            source.rowid,
            str(parsed.get("From", "")),
            recipients,
            str(parsed.get("Subject", "")),
            str(parsed.get("Date", "")) or None,
            str(source.source_date) if source.source_date is not None else None,
            source.raw_message,
            digest,
            datetime.now(timezone.utc).isoformat(),
        )
        cursor = self.db.execute(
            """INSERT OR IGNORE INTO messages (
                identity_key, message_id, account_id, mailbox, apple_rowid,
                sender, recipients, subject, date_header, source_date,
                raw_message, content_sha256, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        if cursor.rowcount == 1:
            content = extract_content(source.raw_message)
            self.db.execute(
                "INSERT INTO search_fts(rowid, title, sender, body, derived) VALUES (?, ?, ?, ?, '')",
                (cursor.lastrowid, str(parsed.get("Subject", "")), str(parsed.get("From", "")), content.text),
            )
        return cursor.rowcount == 1

    def complete_run(self, *, imported: int, skipped: int, errors: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO sync_runs(started_at, finished_at, imported, skipped, errors) VALUES (?, ?, ?, ?, ?)",
            (now, now, imported, skipped, errors),
        )
        # Per-message cache misses are warnings; the index scan itself completed.
        # Keep the checkpoint moving so an uncached old message cannot force a
        # full history scan every fifteen minutes.
        self.db.execute(
            "INSERT INTO sync_state(id, last_success_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_success_at=excluded.last_success_at",
            (now,),
        )
        self.db.commit()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def pending_analysis(self, *, model: str, prompt_version: int, limit: int) -> list[tuple[int, bytes]]:
        return [
            (int(row[0]), row[1])
            for row in self.db.execute(
                """SELECT m.id, m.raw_message FROM messages AS m
                   WHERE NOT EXISTS (
                     SELECT 1 FROM analyses AS a
                     WHERE a.message_id = m.id AND a.model = ? AND a.prompt_version = ?
                   ) AND NOT EXISTS (
                     SELECT 1 FROM analysis_failures AS f
                     WHERE f.message_id=m.id AND f.model=? AND f.prompt_version=?
                       AND f.retry_after > ?
                   ) ORDER BY CAST(m.source_date AS REAL) DESC, m.id DESC LIMIT ?""",
                (model, prompt_version, model, prompt_version,
                 datetime.now(timezone.utc).isoformat(), limit),
            )
        ]

    def mark_analysis_failure(self, *, message_id: int, model: str, prompt_version: int) -> None:
        row = self.db.execute(
            """SELECT attempts FROM analysis_failures
               WHERE message_id=? AND model=? AND prompt_version=?""",
            (message_id, model, prompt_version),
        ).fetchone()
        attempts = int(row[0]) + 1 if row else 1
        delay = min(900 * 4 ** min(attempts - 1, 4), 86400)
        retry_after = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        self.db.execute(
            """INSERT INTO analysis_failures(message_id, model, prompt_version, attempts, retry_after)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(message_id, model, prompt_version) DO UPDATE SET
                   attempts=excluded.attempts, retry_after=excluded.retry_after""",
            (message_id, model, prompt_version, attempts, retry_after),
        )
        self.db.commit()

    def pending_analysis_for_day(self, *, day: str, model: str, prompt_version: int,
                                 timezone_name: str | None = None,
                                 include_deferred: bool = True) -> int:
        zone = ZoneInfo(timezone_name or configured_timezone_name())
        rows = self.db.execute(
            """SELECT m.date_header, m.source_date, m.imported_at, f.retry_after
               FROM messages AS m
               LEFT JOIN analysis_failures AS f ON f.message_id=m.id AND f.model=?
                 AND f.prompt_version=?
               WHERE NOT EXISTS (
                 SELECT 1 FROM analyses AS a
                 WHERE a.message_id = m.id AND a.model = ? AND a.prompt_version = ?
               )""",
            (model, prompt_version, model, prompt_version),
        )
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        for date_header, source_date, imported_at, retry_after in rows:
            if not include_deferred and retry_after and retry_after > now:
                continue
            stamp = parsedate_utc(date_header) if date_header else None
            if stamp is None and source_date:
                try:
                    value = float(source_date)
                    if value > 100_000_000_000:
                        value /= 1000
                    if value < 1_300_000_000:
                        value += 978_307_200
                    stamp = datetime.fromtimestamp(value, timezone.utc)
                except (ValueError, OverflowError):
                    pass
            stamp = stamp or datetime.fromisoformat(imported_at).astimezone(timezone.utc)
            if stamp.astimezone(zone).date().isoformat() == day:
                count += 1
        return count

    def get_analysis_chunk(self, *, message_id: int, model: str, prompt_version: int,
                           chunk_index: int, chunk_sha256: str) -> dict | None:
        row = self.db.execute(
            """SELECT result_json FROM analysis_chunks WHERE message_id=? AND model=?
               AND prompt_version=? AND chunk_index=? AND chunk_sha256=?""",
            (message_id, model, prompt_version, chunk_index, chunk_sha256),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_analysis_chunk(self, *, message_id: int, model: str, prompt_version: int,
                            chunk_index: int, chunk_sha256: str, result: dict) -> None:
        self.db.execute(
            """INSERT INTO analysis_chunks(message_id, model, prompt_version, chunk_index,
                   chunk_sha256, result_json) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id, model, prompt_version, chunk_index) DO UPDATE SET
                   chunk_sha256=excluded.chunk_sha256, result_json=excluded.result_json""",
            (message_id, model, prompt_version, chunk_index, chunk_sha256,
             json.dumps(result, ensure_ascii=False)),
        )
        self.db.commit()

    def save_analysis(self, *, message_id: int, model: str, prompt_version: int, result: dict, selected_images: int) -> None:
        self.db.execute(
            """INSERT INTO analyses(message_id, model, prompt_version, result_json, selected_images, processed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id, model, prompt_version) DO UPDATE SET
                   result_json=excluded.result_json,
                   selected_images=excluded.selected_images,
                   processed_at=excluded.processed_at""",
            (message_id, model, prompt_version, json.dumps(result, ensure_ascii=False), selected_images,
             datetime.now(timezone.utc).isoformat()),
        )
        original = self.db.execute(
            "SELECT subject, sender, raw_message FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if original:
            derived = _derived_text(result)
            self.db.execute("DELETE FROM search_fts WHERE rowid = ?", (message_id,))
            self.db.execute(
                "INSERT INTO search_fts(rowid, title, sender, body, derived) VALUES (?, ?, ?, ?, ?)",
                (message_id, original[0], original[1], extract_content(original[2]).text, derived),
            )
        self.db.execute(
            "DELETE FROM analysis_chunks WHERE message_id=? AND model=? AND prompt_version=?",
            (message_id, model, prompt_version),
        )
        self.db.execute(
            "DELETE FROM analysis_failures WHERE message_id=? AND model=? AND prompt_version=?",
            (message_id, model, prompt_version),
        )
        self.db.commit()

    def analyzed_count(self) -> int:
        return int(self.db.execute("SELECT COUNT(DISTINCT message_id) FROM analyses").fetchone()[0])

    def unpublished_analyses(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            """SELECT m.id, m.sender, m.subject, m.date_header, a.result_json, m.raw_message,
                      COALESCE(h.status, '未处理')
               FROM messages AS m
               JOIN analyses AS a ON a.message_id = m.id
               LEFT JOIN handling AS h ON h.message_id = m.id
               WHERE a.id = (SELECT MAX(a2.id) FROM analyses AS a2 WHERE a2.message_id = m.id)
                 AND json_extract(a.result_json, '$.useful') = 1
                 AND NOT EXISTS (SELECT 1 FROM feishu_records AS f WHERE f.message_id = m.id)
               ORDER BY m.id LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {"id": int(row[0]), "sender": row[1], "subject": row[2], "date": row[3],
             "analysis": json.loads(row[4]), "raw_message": row[5], "status": row[6]}
            for row in rows
        ]

    def pending_feishu(self, *, limit: int = 20, schema_version: int = 3) -> list[dict]:
        rows = self.db.execute(
            """SELECT m.id, m.account_id, m.mailbox, m.sender, m.subject,
                      m.date_header, m.raw_message, COALESCE(h.status, '未处理'),
                      a.id, a.result_json, f.record_id, f.schema_version,
                      f.synced_analysis_id, a.processed_at, f.published_at
               FROM messages AS m
               LEFT JOIN analyses AS a ON a.id = (
                   SELECT MAX(a2.id) FROM analyses AS a2 WHERE a2.message_id=m.id)
               LEFT JOIN handling AS h ON h.message_id=m.id
               LEFT JOIN feishu_records AS f ON f.message_id=m.id
               WHERE f.message_id IS NULL OR f.schema_version < ?
                  OR COALESCE(f.synced_analysis_id, 0) <> COALESCE(a.id, 0)
                  OR (a.processed_at IS NOT NULL AND a.processed_at > f.published_at)
               ORDER BY m.id DESC LIMIT ?""",
            (schema_version, limit),
        ).fetchall()
        return [{
            "id": int(row[0]), "account_id": row[1], "mailbox": row[2],
            "sender": row[3], "subject": row[4], "date": row[5],
            "raw_message": row[6], "status": row[7],
            "analysis_id": row[8], "analysis": json.loads(row[9]) if row[9] else None,
            "record_id": row[10], "schema_version": row[11],
            "synced_analysis_id": row[12], "analysis_processed_at": row[13],
            "published_at": row[14],
        } for row in rows]

    def mark_published(self, message_id: int, record_id: str, *,
                       analysis_id: int | None = None, schema_version: int = 1) -> None:
        self.db.execute(
            """INSERT INTO feishu_records(message_id, record_id, published_at,
                                          synced_analysis_id, schema_version)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET
                   record_id=excluded.record_id, published_at=excluded.published_at,
                   synced_analysis_id=excluded.synced_analysis_id,
                   schema_version=excluded.schema_version""",
            (message_id, record_id, datetime.now(timezone.utc).isoformat(),
             analysis_id, schema_version),
        )
        self.db.commit()

    def pending_notion(self, *, limit: int = 20, schema_version: int = 1) -> list[dict]:
        rows = self.db.execute(
            """SELECT m.id, m.account_id, m.mailbox, m.sender, m.subject,
                      m.date_header, m.raw_message, COALESCE(h.status, '未处理'),
                      a.id, a.result_json, n.page_id, n.schema_version,
                      n.synced_analysis_id, a.processed_at, n.published_at,
                      h.updated_at
               FROM messages AS m
               LEFT JOIN analyses AS a ON a.id = (
                   SELECT MAX(a2.id) FROM analyses AS a2 WHERE a2.message_id=m.id)
               LEFT JOIN handling AS h ON h.message_id=m.id
               LEFT JOIN notion_records AS n ON n.message_id=m.id
               WHERE n.message_id IS NULL OR n.schema_version < ?
                  OR COALESCE(n.synced_analysis_id, 0) <> COALESCE(a.id, 0)
                  OR (a.processed_at IS NOT NULL AND a.processed_at > n.published_at)
                  OR (h.updated_at IS NOT NULL AND h.updated_at > n.published_at)
               ORDER BY m.id DESC LIMIT ?""",
            (schema_version, limit),
        ).fetchall()
        return [{
            "id": int(row[0]), "account_id": row[1], "mailbox": row[2],
            "sender": row[3], "subject": row[4], "date": row[5],
            "raw_message": row[6], "status": row[7],
            "analysis_id": row[8], "analysis": json.loads(row[9]) if row[9] else None,
            "page_id": row[10], "schema_version": row[11],
            "synced_analysis_id": row[12], "analysis_processed_at": row[13],
            "published_at": row[14], "handling_updated_at": row[15],
        } for row in rows]

    def mark_notion_published(self, message_id: int, page_id: str, *,
                              analysis_id: int | None = None,
                              schema_version: int = 1) -> None:
        self.db.execute(
            """INSERT INTO notion_records(message_id, page_id, published_at,
                                           synced_analysis_id, schema_version)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET
                   page_id=excluded.page_id, published_at=excluded.published_at,
                   synced_analysis_id=excluded.synced_analysis_id,
                   schema_version=excluded.schema_version""",
            (message_id, page_id, datetime.now(timezone.utc).isoformat(),
             analysis_id, schema_version),
        )
        self.db.commit()

    def set_status(self, message_id: int, status: str) -> None:
        if status not in {"未处理", "处理中", "已完成", "忽略"}:
            raise ValueError("invalid handling status")
        current = self.db.execute("SELECT status FROM handling WHERE message_id=?",
                                  (message_id,)).fetchone()
        if current and current[0] == status:
            return
        self.db.execute(
            """INSERT INTO handling(message_id, status, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at""",
            (message_id, status, datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def record_mapping(self) -> dict[str, int]:
        return {row[0]: int(row[1]) for row in self.db.execute("SELECT record_id, message_id FROM feishu_records")}

    def search(self, text: str, *, days: int | None = None, limit: int = 20) -> list[dict]:
        if not text.strip():
            raise ValueError("search text must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("search limit must be between 1 and 100")
        query = "%" + text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.db.execute(
            """SELECT m.id, m.sender, m.subject, m.date_header,
                      (SELECT result_json FROM analyses WHERE message_id=m.id ORDER BY id DESC LIMIT 1),
                      COALESCE(h.status, '未处理')
               FROM search_fts AS f JOIN messages AS m ON m.id=f.rowid
               LEFT JOIN handling AS h ON h.message_id=m.id
               WHERE f.title LIKE ? ESCAPE '\\' OR f.sender LIKE ? ESCAPE '\\'
                  OR f.body LIKE ? ESCAPE '\\' OR f.derived LIKE ? ESCAPE '\\'
               ORDER BY CAST(m.source_date AS REAL) DESC, m.id DESC""",
            (query, query, query, query),
        ).fetchall()
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400 if days else None
        results: list[dict] = []
        for row in rows:
            parsed_date = parsedate_utc(row[3]) if row[3] else None
            if cutoff and parsed_date and parsed_date.timestamp() < cutoff:
                continue
            analysis = json.loads(row[4]) if row[4] else {}
            results.append({"id": row[0], "sender": row[1], "subject": row[2], "date": row[3],
                            "title_zh": analysis.get("title_zh", ""),
                            "summary_zh": analysis.get("summary_zh", ""),
                            "action_items": analysis.get("action_items", []), "status": row[5]})
            if len(results) >= limit:
                break
        return results

    def recent_analyses(self, *, days: int | None = None, limit: int = 100) -> list[dict]:
        if days is not None and days < 1:
            raise ValueError("days must be at least 1")
        rows = self.db.execute(
            """SELECT m.id, m.sender, m.subject, m.date_header, m.imported_at, a.result_json,
                      COALESCE(h.status, '未处理')
               FROM messages AS m JOIN analyses AS a ON a.message_id=m.id
               LEFT JOIN handling AS h ON h.message_id=m.id
               WHERE a.id=(SELECT MAX(a2.id) FROM analyses AS a2 WHERE a2.message_id=m.id)
               ORDER BY CAST(m.source_date AS REAL) DESC, m.id DESC""",
        )
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400 if days else None
        result: list[dict] = []
        for ident, sender, subject, date_header, imported_at, raw_analysis, status in rows:
            stamp = parsedate_utc(date_header) if date_header else None
            stamp = stamp or datetime.fromisoformat(imported_at).astimezone(timezone.utc)
            if cutoff and stamp.timestamp() < cutoff:
                continue
            analysis = json.loads(raw_analysis)
            if not analysis.get("useful"):
                continue
            result.append({"id": ident, "sender": sender, "subject": subject, "date": date_header,
                           "title_zh": analysis["title_zh"], "summary_zh": analysis["summary_zh"],
                           "action_items": analysis["action_items"], "status": status})
            if len(result) >= limit:
                break
        return result

    def brief_items(self, day: str, *, timezone_name: str | None = None) -> list[dict]:
        zone = ZoneInfo(timezone_name or configured_timezone_name())
        rows = self.db.execute(
            """SELECT m.sender, m.subject, m.date_header, m.imported_at, a.result_json,
                      COALESCE(h.status, '未处理')
               FROM messages AS m JOIN analyses AS a ON a.message_id=m.id
               LEFT JOIN handling AS h ON h.message_id=m.id
               WHERE a.id=(SELECT MAX(a2.id) FROM analyses AS a2 WHERE a2.message_id=m.id)
               ORDER BY m.id"""
        )
        result: list[dict] = []
        for sender, subject, date_header, imported_at, raw_analysis, status in rows:
            stamp = parsedate_utc(date_header) if date_header else None
            stamp = stamp or datetime.fromisoformat(imported_at).astimezone(timezone.utc)
            if stamp.astimezone(zone).date().isoformat() != day:
                continue
            analysis = json.loads(raw_analysis)
            if not analysis.get("useful"):
                continue
            result.append({
                "sender": sender, "subject": subject, "date": stamp.isoformat(),
                "title_zh": analysis["title_zh"], "summary_zh": analysis["summary_zh"],
                "action_required": analysis["action_required"],
                "action_items": analysis["action_items"], "status": status,
                "remote_images_not_loaded": (analysis.get("_source_images") or {}).get("remote_not_loaded", 0),
            })
        return result

    def save_brief(self, day: str, body: str, model: str, usage: dict) -> None:
        self.db.execute(
            """INSERT INTO daily_briefs(day, body, model, usage_json, generated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET body=excluded.body, model=excluded.model,
                   usage_json=excluded.usage_json, generated_at=excluded.generated_at,
                   feishu_sent_at=NULL, email_sent_at=NULL""",
            (day, body, model, json.dumps(usage), datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def get_brief(self, day: str) -> dict | None:
        row = self.db.execute(
            "SELECT body, model, usage_json, generated_at, feishu_sent_at, email_sent_at "
            "FROM daily_briefs WHERE day=?", (day,)
        ).fetchone()
        return ({"body": row[0], "model": row[1], "usage": json.loads(row[2]),
                 "generated_at": row[3], "feishu_sent_at": row[4],
                 "email_sent_at": row[5]} if row else None)

    def mark_brief_sent(self, day: str, channel: str) -> None:
        if channel not in {"feishu", "email"}:
            raise ValueError("invalid brief channel")
        self.db.execute(
            f"UPDATE daily_briefs SET {channel}_sent_at=? WHERE day=?",
            (datetime.now(timezone.utc).isoformat(), day),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
