from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.apple_mail import SourceMessage
from clover_mail.archive import Archive
from clover_mail.mimo import MiMoConfig
from clover_mail.query import _asks_open, _plan, ask


class QueryTests(unittest.TestCase):
    def test_openai_name_does_not_mean_open_tasks(self):
        self.assertFalse(_asks_open("OpenAI 最近有什么活动？"))
        self.assertTrue(_asks_open("最近有什么 open tasks?"))

    def test_query_plan_has_local_fallback_on_invalid_model_output(self):
        config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        with patch("clover_mail.query._request", return_value=("not-json", {})):
            plan, _usage = _plan("最近两周 Claude Certification 有哪些未完成事项？", config)
        self.assertEqual(plan["since_days"], 14)
        self.assertTrue(plan["open_only"])
        self.assertIn("Claude Certification", plan["entity_terms"])
        self.assertTrue(plan["_local_fallback"])

    def test_null_open_only_is_inferred_from_question(self):
        config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
        response = json.dumps({"entity_terms": ["Claude Certification"], "topic_terms": ["认证"],
                               "since_days": None, "open_only": None})
        with patch("clover_mail.query._request", return_value=(response, {})):
            plan, _usage = _plan("帮我找 Claude Certification 相关邮件", config)
        self.assertFalse(plan["open_only"])

    def test_search_uses_mail_time_even_when_history_is_imported_later(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            for rowid in range(111):
                message = EmailMessage()
                message["From"] = "news@example.edu"
                message["Subject"] = "Shared keyword"
                message.set_content("Synthetic content")
                archive.add(SourceMessage("account", "INBOX", rowid, 200 if rowid == 0 else 100,
                                          message.as_bytes()))
            archive.db.commit()
            self.assertEqual(archive.search("Shared keyword", limit=1)[0]["id"], 1)
            archive.close()

    def test_generic_open_task_words_use_recent_action_items(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            message = EmailMessage()
            message["From"] = "Teacher <teacher@example.edu>"
            message["Subject"] = "Registration deadline"
            message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
            message.set_content("Register by October 6.")
            archive.add(SourceMessage("school", "INBOX", 1, None, message.as_bytes()))
            archive.db.commit()
            ident = archive.db.execute("SELECT id FROM messages").fetchone()[0]
            archive.save_analysis(message_id=ident, model="mimo-v2.6-flash", prompt_version=1, selected_images=0,
                                  result={"useful": True, "title_zh": "注册截止日期", "summary_zh": "请注册课程",
                                          "translation_zh": "请在十月六日前注册。", "action_required": True,
                                          "action_items": [{"action": "完成课程注册", "deadline_iso": "2026-10-06"}],
                                          "important_facts": []})
            config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
            plan = {"entity_terms": [], "topic_terms": ["待处理", "未完成", "待办"], "since_days": 14, "open_only": True}
            with patch("clover_mail.query._request", return_value=(json.dumps(plan), {})):
                self.assertEqual([item["id"] for item in ask(archive, "最近两周未完成的事", config=config)["matches"]], [ident])
            archive.close()

    def test_natural_plan_intersects_entity_topic_and_open_state(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            message = EmailMessage()
            message["From"] = "Campus Office <office@example.edu>"
            message["Subject"] = "Course Registration"
            message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
            message.set_content("Register for your classes.")
            archive.add(SourceMessage("campus", "INBOX", 1, None, message.as_bytes()))
            archive.db.commit()
            ident = archive.db.execute("SELECT id FROM messages").fetchone()[0]
            archive.save_analysis(message_id=ident, model="mimo-v2.6-flash", prompt_version=1, selected_images=0,
                                  result={"useful": True, "title_zh": "课程注册", "summary_zh": "请注册课程",
                                          "translation_zh": "请注册课程。", "action_required": True,
                                          "action_items": [{"action": "完成课程注册", "deadline_iso": None}],
                                          "important_facts": []})
            config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
            plan = {"entity_terms": ["Campus"], "topic_terms": ["注册", "register"],
                    "since_days": 14, "open_only": True}
            with patch("clover_mail.query._request", return_value=(json.dumps(plan), {"total_tokens": 12})):
                result = ask(archive, "Campus 最近两周哪些注册还没做？", config=config)
                self.assertEqual([item["id"] for item in result["matches"]], [ident])
                archive.set_status(ident, "已完成")
                self.assertEqual(ask(archive, "Campus 最近两周哪些注册还没做？", config=config)["matches"], [])
            archive.close()

    def test_search_recovers_when_model_entity_term_has_no_match(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            message = EmailMessage()
            message["From"] = "events@example.edu"
            message["Subject"] = "Campus hackathon"
            message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
            message.set_content("Register for the hackathon.")
            archive.add(SourceMessage("school", "INBOX", 1, None, message.as_bytes()))
            archive.db.commit()
            ident = archive.db.execute("SELECT id FROM messages").fetchone()[0]
            config = MiMoConfig("synthetic-key", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash")
            plan = {"entity_terms": ["AI contest"], "topic_terms": ["hackathon"],
                    "since_days": 30, "open_only": False}
            with patch("clover_mail.query._request", return_value=(json.dumps(plan), {})):
                result = ask(archive, "最近一个月有哪些 AI 比赛？", config=config)
            self.assertEqual([item["id"] for item in result["matches"]], [ident])
            archive.close()


if __name__ == "__main__":
    unittest.main()
