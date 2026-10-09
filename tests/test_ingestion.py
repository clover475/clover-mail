from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.apple_mail import MailSourceError, SourceMessage, _source_cutoff, account_labels, doctor, iter_messages
from clover_mail.archive import Archive
from clover_mail.cli import run_sync


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "Mail" / "V10"
        (self.root / "MailData").mkdir(parents=True)
        self.account = "account-uuid"
        data = self.root / self.account / "INBOX.mbox" / "store-id" / "Data" / "3" / "2" / "1" / "Messages"
        data.mkdir(parents=True)
        self.message_path = data / "123456.emlx"
        message = EmailMessage(policy=policy.default)
        message["From"] = "Teacher <teacher@example.edu>"
        message["To"] = "me@example.com"
        message["Subject"] = "Course registration"
        message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        message["Message-ID"] = "<message-123@example.edu>"
        message.set_content("Register by October 6.")
        self.raw = message.as_bytes(policy=policy.default)
        self.message_path.write_bytes(str(len(self.raw)).encode() + b"\n" + self.raw + b"\n")
        db = sqlite3.connect(self.root / "MailData" / "Envelope Index")
        db.executescript(
            """CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT);
               CREATE TABLE messages (ROWID INTEGER PRIMARY KEY, mailbox INTEGER, date_received INTEGER);
               CREATE TABLE subjects (ROWID INTEGER PRIMARY KEY, subject TEXT);
               CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT);
               INSERT INTO mailboxes VALUES (1, 'imap://account-uuid/INBOX');"""
        )
        db.execute("INSERT INTO messages VALUES (123456, 1, ?)", (int(datetime.now(timezone.utc).timestamp()),))
        db.commit()
        db.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_doctor_detects_source_without_exposing_content(self):
        result = doctor(self.base / "Mail")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["indexed_messages"], 1)
        self.assertNotIn("Subject", str(result))

    def test_source_labels_use_mail_account_metadata(self):
        index = self.root / "MailData" / "Envelope Index"
        db = sqlite3.connect(index)
        db.execute("INSERT INTO mailboxes VALUES (2, 'ews://school-account/Inbox')")
        db.execute("INSERT INTO mailboxes VALUES (3, 'imap://gmail-account/%5BGmail%5D/All%20Mail')")
        db.commit()
        db.close()
        labels = account_labels(self.base / "Mail")
        self.assertEqual(labels["school-account"], "Outlook")
        self.assertEqual(labels["gmail-account"], "Gmail")

    def test_reads_complete_message_and_respects_date(self):
        since = datetime.now(timezone.utc) - timedelta(days=1)
        messages, errors = iter_messages(self.root, since=since)
        self.assertEqual(errors, [])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].account_id, self.account)
        self.assertEqual(messages[0].raw_message, self.raw)

    def test_idempotent_archive(self):
        messages, errors = iter_messages(self.root, since=datetime.now(timezone.utc) - timedelta(days=1))
        self.assertEqual(errors, [])
        with tempfile.TemporaryDirectory() as archive_dir:
            archive = Archive(Path(archive_dir) / "archive")
            self.assertTrue(archive.add(messages[0]))
            self.assertFalse(archive.add(messages[0]))
            self.assertEqual(archive.count(), 1)
            stored = archive.db.execute("SELECT raw_message FROM messages").fetchone()[0]
            self.assertEqual(stored, self.raw)
            archive.close()

    def test_own_daily_brief_is_not_imported(self):
        own = EmailMessage()
        own["Message-ID"] = "<clover-mail-brief-20261009-aabbccdd@local.clover-mail>"
        own["Subject"] = "Clover Mail 日报 · 2026-10-09"
        own.set_content("Synthetic generated brief")
        source = SourceMessage(self.account, "INBOX", 123457, None, own.as_bytes())
        with tempfile.TemporaryDirectory() as directory, \
             patch("clover_mail.cli.discover_mail_root", return_value=self.root), \
             patch("clover_mail.cli.iter_messages", return_value=([source], [])), \
             patch("clover_mail.cli.Archive", side_effect=lambda: Archive(Path(directory))):
            result = run_sync(1)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["own_briefs_ignored"], 1)
        self.assertEqual(result["archive_total"], 0)

    def test_completed_scan_checkpoints_despite_uncached_message_warnings(self):
        with tempfile.TemporaryDirectory() as archive_dir:
            archive = Archive(Path(archive_dir) / "archive")
            archive.complete_run(imported=1, skipped=0, errors=2)
            self.assertIsNotNone(archive.latest_success())
            self.assertEqual(archive.db.execute("SELECT errors FROM sync_runs").fetchone()[0], 2)
            archive.close()

    def test_missing_source_fails_with_actionable_error(self):
        empty_mail = self.base / "empty"
        empty_mail.mkdir()
        with self.assertRaisesRegex(MailSourceError, "No local Apple Mail index"):
            doctor(empty_mail)

    def test_source_cutoff_uses_detected_mail_index_clock(self):
        since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.assertEqual(_source_cutoff(since, 1_800_000_000), int(since.timestamp()))
        self.assertEqual(_source_cutoff(since, 900_000_000), int(since.timestamp()) - 978_307_200)
        self.assertEqual(_source_cutoff(since, 1_800_000_000_000), int(since.timestamp() * 1000))


if __name__ == "__main__":
    unittest.main()
