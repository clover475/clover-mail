from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.apple_mail import SourceMessage
from clover_mail.archive import Archive
from clover_mail.brief import generate, preview, send_to_email, send_to_feishu, today_local
from clover_mail.email_delivery import SMTPConfig, send_brief
from clover_mail.ai import AIConfig


class FakeClient:
    def __init__(self, open_id="ou_owner"):
        self.sent = []
        self.config = SimpleNamespace(open_id=open_id)

    def send_text(self, text, *, open_id=None):
        self.sent.append((text, open_id))


class BriefTests(unittest.TestCase):
    def test_updated_brief_has_distinct_message_id(self):
        smtp = SMTPConfig("smtp.example.com", 465, "user", "synthetic", "from@example.com", "to@example.com")
        with patch("clover_mail.email_delivery.smtplib.SMTP_SSL") as factory:
            send_brief(day="2026-10-09", body="Early synthetic brief", config=smtp)
            send_brief(day="2026-10-09", body="Final synthetic brief", config=smtp)
            send_brief(day="2026-10-09", body="Final synthetic brief", config=smtp)
        messages = [call.args[0] for call in factory.return_value.__enter__.return_value.send_message.call_args_list]
        self.assertNotEqual(messages[0]["Message-ID"], messages[1]["Message-ID"])
        self.assertEqual(messages[1]["Message-ID"], messages[2]["Message-ID"])

    def test_brief_discloses_unloaded_remote_images(self):
        archive = MagicMock()
        archive.get_brief.return_value = None
        archive.pending_analysis_for_day.return_value = 0
        facts = {"day": "2026-10-09", "valuable_mail": 1, "requires_action": 0,
                 "new_action_items": 0, "deadline_items": 0,
                 "items": [{"summary_zh": "合成摘要", "remote_images_not_loaded": 3}]}
        config = AIConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        with patch("clover_mail.brief.preview", return_value=facts), \
             patch("clover_mail.brief._request", return_value=("日报正文", {})):
            generate(archive, day="2026-10-09", config=config)
        self.assertIn("未加载的远程图片", archive.save_brief.call_args.args[1])

    def test_brief_is_generated_once_and_each_channel_sends_once(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            message = EmailMessage()
            message["From"] = "Teacher <teacher@example.edu>"
            message["Subject"] = "Registration"
            message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
            message.set_content("Register by October 6.")
            archive.add(SourceMessage("account", "INBOX", 1, None, message.as_bytes()))
            archive.db.commit()
            message_id = archive.db.execute("SELECT id FROM messages").fetchone()[0]
            archive.save_analysis(message_id=message_id, model="mimo-v2.6-flash", prompt_version=1, selected_images=0,
                                  result={"useful": True, "title_zh": "注册", "summary_zh": "请完成注册",
                                          "translation_zh": "请在十月六日前注册。", "action_required": True,
                                          "action_items": [{"action": "完成注册", "deadline_iso": "2026-10-06"}],
                                          "important_facts": []})
            day = today_local()
            self.assertEqual(preview(archive, day)["valuable_mail"], 1)
            config = AIConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
            with patch("clover_mail.brief._request", return_value=("优先完成注册。", {"total_tokens": 99})) as model:
                self.assertTrue(generate(archive, day=day, config=config)["created"])
                self.assertFalse(generate(archive, day=day, config=config)["created"])
                archive.set_status(message_id, "处理中")
                self.assertTrue(generate(archive, day=day, config=config)["created"])
            self.assertEqual(model.call_count, 2)
            client = FakeClient()
            self.assertTrue(send_to_feishu(archive, client, day=day)["sent"])
            self.assertFalse(send_to_feishu(archive, client, day=day)["sent"])
            self.assertEqual(len(client.sent), 1)
            self.assertEqual(client.sent[0][1], "ou_owner")
            smtp = SMTPConfig("smtp.example.com", 465, "user", "synthetic", "from@example.com", "to@example.com")
            with patch("clover_mail.brief.send_brief") as sender:
                self.assertTrue(send_to_email(archive, smtp, day=day)["sent"])
                self.assertFalse(send_to_email(archive, smtp, day=day)["sent"])
            self.assertEqual(sender.call_count, 1)
            with patch("clover_mail.brief._request") as model:
                self.assertFalse(generate(archive, day=day, config=config)["created"])
                model.assert_not_called()
            archive.close()

    def test_brief_refuses_group_fallback_without_personal_open_id(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            day = today_local()
            archive.save_brief(day, "Synthetic private brief", "test-model", {})
            client = FakeClient(open_id="")
            with self.assertRaisesRegex(ValueError, "FEISHU_OPEN_ID"):
                send_to_feishu(archive, client, day=day)
            self.assertEqual(client.sent, [])
            archive.close()

    def test_evening_pass_replaces_changed_early_validation_once(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            day = "2026-10-09"
            archive.save_brief(day, "Synthetic early brief", "mimo-v2.6-flash",
                               {"_source_sha256": "old"})
            archive.mark_brief_sent(day, "feishu")
            archive.db.execute("UPDATE daily_briefs SET generated_at=? WHERE day=?",
                               ("2026-10-09T13:00:00+00:00", day))
            archive.db.commit()
            facts = {"day": day, "valuable_mail": 1, "requires_action": 0,
                     "new_action_items": 0, "deadline_items": 0,
                     "items": [{"summary_zh": "New synthetic fact"}]}
            config = AIConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
            with patch("clover_mail.brief.preview", return_value=facts), \
                 patch("clover_mail.brief._request", return_value=("Final synthetic brief", {})) as model:
                self.assertFalse(generate(archive, day=day, config=config)["created"])
                self.assertTrue(generate(archive, day=day, config=config,
                                         finalize_early=True)["created"])
            model.assert_called_once()
            self.assertIsNone(archive.get_brief(day)["feishu_sent_at"])
            archive.mark_brief_sent(day, "feishu")
            archive.db.execute("UPDATE daily_briefs SET generated_at=? WHERE day=?",
                               ("2026-10-09T21:00:00+00:00", day))
            archive.db.commit()
            facts["items"].append({"summary_zh": "Later synthetic fact"})
            with patch("clover_mail.brief.preview", return_value=facts), \
                 patch("clover_mail.brief._request") as model:
                self.assertFalse(generate(archive, day=day, config=config,
                                          finalize_early=True)["created"])
                model.assert_not_called()
            archive.close()


if __name__ == "__main__":
    unittest.main()
