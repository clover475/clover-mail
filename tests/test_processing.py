from __future__ import annotations

import sys
import tempfile
import os
import unittest
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.apple_mail import SourceMessage
from clover_mail.archive import Archive
from clover_mail.content import extract_content
from clover_mail.mimo import MiMoConfig, MiMoError
from clover_mail.processing import _analyze_long_email, _analyze_section, analyze_pending


def fake_png(width: int, height: int, size: int = 12_000) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"x" * size


def newsletter() -> bytes:
    message = EmailMessage()
    message["From"] = "Events <events@example.edu>"
    message["To"] = "me@example.com"
    message["Subject"] = "Hackathon"
    message["Message-ID"] = "<hackathon@example.edu>"
    message.set_content("Register for the event by October 6.")
    message.add_alternative(
        '<p>Register for the event by October 6.</p><img src="https://tracker.example/pixel" alt="logo">'
        '<img src="cid:poster" alt="Hackathon poster with schedule">',
        subtype="html",
    )
    message.add_attachment(fake_png(800, 1100), maintype="image", subtype="png", filename="hackathon-poster.png")
    message.add_attachment(fake_png(32, 32), maintype="image", subtype="png", filename="tiny.png")
    message.add_attachment(fake_png(800, 1100), maintype="image", subtype="png", filename="logo.png")
    return message.as_bytes()


class ProcessingTests(unittest.TestCase):
    def test_bad_json_section_is_retried_as_smaller_sections(self):
        config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        body = "Event schedule information.\n" * 140
        def fake_analysis(*, body, **_kwargs):
            if len(body) > 2_000:
                raise MiMoError("MiMo response was not valid JSON")
            return {"useful": True, "title_zh": "活动", "summary_zh": "活动日程",
                    "translation_zh": "中文:" + body, "action_required": False,
                    "action_items": [], "important_facts": [], "_usage": {"total_tokens": 10}}
        with patch("clover_mail.processing.analyze_email", side_effect=fake_analysis) as calls:
            result = _analyze_section(sender="", subject="", date="", body=body,
                                      images=(), config=config)
        self.assertEqual(calls.call_count, 3)
        self.assertEqual(result["_usage"]["total_tokens"], 20)
        self.assertEqual(result["translation_zh"].count("中文:"), 2)

    def test_long_email_merges_complete_chunk_translations_and_actions(self):
        config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        body = ("Please register for the event by October 6.\n" * 180)
        def fake_analysis(*, body, **_kwargs):
            return {"useful": True, "title_zh": "活动注册", "summary_zh": "请注册活动",
                    "translation_zh": "中文:" + body, "action_required": True,
                    "action_items": [{"action": "注册活动", "deadline_iso": "2026-10-06", "evidence": "by October 6"}],
                    "important_facts": ["活动截止 10 月 6 日"], "_usage": {"total_tokens": 10}}
        with patch("clover_mail.processing.analyze_email", side_effect=fake_analysis) as calls, \
             patch("clover_mail.processing._request", return_value=("请在 10 月 6 日前注册活动。", {"total_tokens": 5})):
            result = _analyze_long_email(sender="", subject="Event", date="", body=body, images=(), config=config)
        self.assertGreater(calls.call_count, 1)
        self.assertTrue(all(call.kwargs["config"].max_tokens == 8192 for call in calls.call_args_list))
        self.assertEqual(result["_chunk_count"], calls.call_count)
        self.assertEqual(len(result["action_items"]), 1)
        self.assertEqual(result["_usage"]["total_tokens"], 10 * calls.call_count + 5)
        self.assertEqual(result["translation_zh"].count("中文:"), calls.call_count)

    def test_interrupted_long_email_resumes_saved_chunks(self):
        config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        body = "English newsletter paragraph.\n" * 270
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            mail = EmailMessage()
            mail["From"] = "events@example.edu"
            mail["Subject"] = "Newsletter"
            mail.set_content(body)
            archive.add(SourceMessage("account", "INBOX", 1, None, mail.as_bytes()))
            archive.db.commit()
            calls = 0
            def fake_analysis(*, body, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ValueError("interrupted")
                return {"useful": True, "title_zh": "通讯", "summary_zh": "摘要",
                        "translation_zh": "中文:" + body, "action_required": False,
                        "action_items": [], "important_facts": [], "_usage": {}}
            with patch("clover_mail.processing.analyze_email", side_effect=fake_analysis), \
                 patch("clover_mail.processing._request", return_value=("摘要", {})):
                with self.assertRaises(ValueError):
                    _analyze_long_email(sender="", subject="", date="", body=body,
                                        images=(), config=config, archive=archive, message_id=1)
                result = _analyze_long_email(sender="", subject="", date="", body=body,
                                             images=(), config=config, archive=archive, message_id=1)
            self.assertEqual(result["_chunk_count"], 3)
            self.assertEqual(calls, 4)
            archive.close()

    def test_today_pending_count_ignores_older_backlog(self):
        config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            local_noon = datetime.now(timezone(timedelta(hours=8))).replace(
                hour=12, minute=0, second=0, microsecond=0,
            )
            for rowid, days_ago in ((1, 0), (2, 1)):
                mail = EmailMessage()
                mail["From"] = "events@example.edu"
                mail["Subject"] = f"Message {rowid}"
                mail["Date"] = format_datetime(local_noon - timedelta(days=days_ago))
                mail.set_content("Synthetic mail")
                archive.add(SourceMessage("account", "INBOX", rowid, None, mail.as_bytes()))
            archive.db.commit()
            today = local_noon.date().isoformat()
            self.assertEqual(archive.pending_analysis_for_day(day=today, model=config.model,
                                                               prompt_version=2), 1)
            archive.mark_analysis_failure(message_id=1, model=config.model, prompt_version=2)
            self.assertEqual(archive.pending_analysis_for_day(day=today, model=config.model,
                                                               prompt_version=2,
                                                               include_deferred=False), 0)
            archive.close()

    def test_medium_newsletter_uses_chunked_translation(self):
        config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        message = EmailMessage()
        message["From"] = "events@example.edu"
        message["Subject"] = "Newsletter"
        message.set_content("Event information and schedule.\n" * 160)
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            archive.add(SourceMessage("account", "INBOX", 1, None, message.as_bytes()))
            archive.db.commit()
            result = {"useful": True, "title_zh": "活动", "summary_zh": "活动信息",
                      "translation_zh": "中文全文", "action_required": False,
                      "action_items": [], "important_facts": []}
            with patch("clover_mail.processing._analyze_long_email", return_value=result) as chunks, \
                 patch("clover_mail.processing.analyze_email") as single:
                self.assertEqual(analyze_pending(archive, config=config)["analyzed"], 1)
            chunks.assert_called_once()
            single.assert_not_called()
            archive.close()

    def test_failed_analysis_waits_before_scheduled_retry(self):
        config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        message = EmailMessage()
        message["From"] = "events@example.edu"
        message["Subject"] = "Event"
        message.set_content("Synthetic event")
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            archive.add(SourceMessage("account", "INBOX", 1, None, message.as_bytes()))
            archive.db.commit()
            archive.mark_analysis_failure(message_id=1, model=config.model, prompt_version=2)
            self.assertEqual(archive.pending_analysis(model=config.model, prompt_version=2, limit=5), [])
            archive.db.execute("UPDATE analysis_failures SET retry_after='2000-01-01T00:00:00+00:00'")
            archive.db.commit()
            self.assertEqual(len(archive.pending_analysis(model=config.model, prompt_version=2, limit=5)), 1)
            archive.close()

    def test_extracts_text_and_informative_local_image_without_remote_fetch(self):
        result = extract_content(newsletter())
        self.assertIn("Register for the event by October 6.", result.text)
        self.assertIn("Hackathon poster with schedule", result.text)
        self.assertNotIn("logo", result.text)
        self.assertEqual(result.remote_image_count, 1)
        self.assertEqual(len(result.images), 1)
        self.assertEqual(result.images[0].filename, "hackathon-poster.png")
        self.assertEqual((result.images[0].width, result.images[0].height), (800, 1100))

    def test_remote_image_is_only_a_candidate_until_selective_mode_enabled(self):
        message = EmailMessage()
        message["From"] = "events@example.edu"
        message["Subject"] = "Event"
        message.set_content("Event details")
        message.add_alternative(
            '<p>Event details</p><img src="https://cdn.example.org/poster.png" '
            'alt="Event poster" width="800" height="1000">'
            '<img src="https://track.example.org/pixel.gif" alt="logo" width="1" height="1">',
            subtype="html",
        )
        extracted = extract_content(message.as_bytes())
        self.assertEqual(extracted.remote_image_count, 2)
        self.assertEqual(len(extracted.remote_candidates), 1)
        self.assertEqual(extracted.images, ())
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            archive.add(SourceMessage("account", "INBOX", 1, None, message.as_bytes()))
            archive.db.commit()
            config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
            result = {"useful": True, "title_zh": "活动", "summary_zh": "活动详情",
                      "translation_zh": "活动详情", "action_required": False,
                      "action_items": [], "important_facts": []}
            with patch.dict(os.environ, {"CLOVER_MAIL_REMOTE_IMAGES": ""}), \
                 patch("clover_mail.processing.fetch_selected") as fetch, \
                 patch("clover_mail.processing.analyze_email", return_value=result):
                analyzed = analyze_pending(archive, config=config)
            self.assertEqual(analyzed["remote_candidates"], 1)
            fetch.assert_not_called()
            archive.close()

    def test_analysis_is_saved_once_and_keeps_source_private(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            source = SourceMessage("account", "INBOX", 42, None, newsletter())
            archive.add(source)
            archive.db.commit()
            config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
            result = {
                "useful": True, "title_zh": "活动", "summary_zh": "请报名", "translation_zh": "请在十月六日前报名。",
                "action_required": True, "action_items": [{"action": "报名", "deadline_iso": "2026-10-06", "evidence": "by October 6"}],
                "important_facts": [],
            }
            with patch("clover_mail.processing.analyze_email", return_value=result) as call:
                preview = analyze_pending(archive, config=config, dry_run=True)
                self.assertEqual(preview["selected_local_images"], 1)
                self.assertEqual(preview["remote_images_not_loaded"], 1)
                self.assertEqual(call.call_count, 0)
                first = analyze_pending(archive, config=config)
                second = analyze_pending(archive, config=config)
            self.assertEqual(first["analyzed"], 1)
            self.assertEqual(second["considered"], 0)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(archive.analyzed_count(), 1)
            stored = archive.db.execute("SELECT result_json, selected_images FROM analyses").fetchone()
            self.assertIn("报名", stored[0])
            self.assertEqual(stored[1], 1)
            self.assertEqual(len(archive.search("Hackathon")), 1)
            self.assertEqual(len(archive.search("请报名")), 1)
            archive.close()


if __name__ == "__main__":
    unittest.main()
