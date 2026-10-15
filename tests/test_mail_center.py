from __future__ import annotations

import sys
import tempfile
import unittest
from email.message import EmailMessage
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.apple_mail import SourceMessage
from clover_mail.archive import Archive
from clover_mail.mail_center import provision, publish_pending, pull_statuses


class FakeFeishu:
    def __init__(self):
        self.records = []
        self.fields = []
        self.views = [{"view_id": "all123", "view_name": "全部邮件"}]

    def create_base(self, name):
        return {"app": {"app_token": "base123", "url": "https://example.feishu.cn/base/base123"}}

    def create_table(self, app_token, name, fields):
        assert app_token == "base123" and name == "邮件"
        assert any(field["field_name"] == "处理状态" for field in fields)
        self.fields = [self._field(field, f"field{index}") for index, field in enumerate(fields)]
        return {"table_id": "table123"}

    @staticmethod
    def _field(definition, field_id):
        field = {**definition, "field_id": field_id}
        if definition.get("type") == 3:
            field["property"] = {"options": [
                {**option, "id": f"opt{index}"}
                for index, option in enumerate(definition["property"]["options"])]}
        return field

    def list_fields(self, app_token, table_id):
        return {"items": self.fields}

    def create_field(self, app_token, table_id, definition):
        field = self._field(definition, f"field{len(self.fields)}")
        self.fields.append(field)
        return {"field": field}

    def list_views(self, app_token, table_id):
        return {"items": self.views}

    def create_view(self, app_token, table_id, name):
        view = {"view_id": "today123" if name == "今天" else f"view{len(self.views) + 1}",
                "view_name": name}
        self.views.append(view)
        return {"view": view}

    def update_view(self, app_token, table_id, view_id, property):
        view = next(view for view in self.views if view["view_id"] == view_id)
        view["property"] = property
        return {"view": view}

    def list_records(self, app_token, table_id, page_token="", field_names=()):
        return {"items": [{"record_id": record["record_id"],
                           "fields": {name: value for name, value in record["fields"].items()
                                      if not field_names or name in field_names}}
                          for record in self.records], "has_more": False}

    def create_record(self, app_token, table_id, fields):
        record = {"record_id": "record123", "fields": fields}
        self.records.append(record)
        return {"record": record}

    def batch_create_records(self, app_token, table_id, fields_list):
        created = []
        for fields in fields_list:
            record = {"record_id": f"record{len(self.records) + 1}", "fields": fields}
            self.records.append(record)
            created.append(record)
        return {"records": created}

    def batch_update_records(self, app_token, table_id, changes):
        for change in changes:
            record = next(item for item in self.records
                          if item["record_id"] == change["record_id"])
            record["fields"].update(change["fields"])
        return {"records": changes}


class MailCenterTests(unittest.TestCase):
    def test_provision_publish_and_pull_status(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            archive = Archive(Path(directory) / "archive")
            message = EmailMessage()
            message["From"] = "Teacher <teacher@example.edu>"
            message["Subject"] = "Registration"
            message["Message-ID"] = "<a@example.edu>"
            message["Date"] = datetime(2026, 10, 6, tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
            message.set_content("Register by October 6.")
            archive.add(SourceMessage("account", "INBOX", 1, None, message.as_bytes()))
            archive.db.commit()
            message_id = archive.db.execute("SELECT id FROM messages").fetchone()[0]
            archive.save_analysis(message_id=message_id, model="mimo-v2.6-flash", prompt_version=1, selected_images=0,
                                  result={"useful": True, "title_zh": "注册提醒", "summary_zh": "请及时注册",
                                          "translation_zh": "请在十月六日前注册。", "action_required": True,
                                          "action_items": [{"action": "完成注册", "deadline_iso": "2026-10-06"}],
                                          "important_facts": []})
            client = FakeFeishu()
            state = provision(client, state_path=state_path)
            self.assertEqual(state["table_id"], "table123")
            self.assertEqual(state["url"], "https://example.feishu.cn/base/base123?table=table123&view=today123")
            today = next(view for view in client.views if view["view_name"] == "今天")
            self.assertEqual(today["property"]["filter_info"]["conditions"][0]["value"], '["Today"]')
            self.assertEqual(provision(client, state_path=state_path)["app_token"], "base123")
            with patch("clover_mail.mail_center.account_labels", return_value={"account": "Outlook"}):
                self.assertEqual(publish_pending(client, archive, state_path=state_path)["published"], 1)
                self.assertEqual(publish_pending(client, archive, state_path=state_path)["published"], 0)
            self.assertEqual(client.records[0]["fields"]["截止日期"], "2026-10-06")
            self.assertIsInstance(client.records[0]["fields"]["收件日期"], int)
            self.assertEqual(client.records[0]["fields"]["来源邮箱"], "Outlook")
            self.assertEqual(client.records[0]["fields"]["AI理解状态"], "已分析")
            client.records[0]["fields"]["处理状态"] = "已完成"
            self.assertEqual(pull_statuses(client, archive, state_path=state_path)["statuses_read"], 1)
            status = archive.db.execute("SELECT status FROM handling").fetchone()[0]
            self.assertEqual(status, "已完成")
            archive.close()

    def test_unanalyzed_mail_gets_row_then_enrichment_preserves_status(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            archive = Archive(Path(directory) / "archive")
            message = EmailMessage()
            message["From"] = "Teacher <teacher@example.edu>"
            message["Subject"] = "Course update"
            message["Message-ID"] = "<plain@example.edu>"
            message.set_content("Synthetic course update")
            archive.add(SourceMessage("outlook", "Inbox", 2, None, message.as_bytes()))
            archive.db.commit()
            client = FakeFeishu()
            provision(client, state_path=state_path)
            with patch("clover_mail.mail_center.account_labels", return_value={"outlook": "Outlook"}):
                self.assertEqual(publish_pending(client, archive, state_path=state_path)["published"], 1)
                self.assertEqual(client.records[0]["fields"]["AI理解状态"], "待分析")
                self.assertIn("Synthetic", client.records[0]["fields"]["原文内容"])
                client.records[0]["fields"]["处理状态"] = "处理中"
                archive.save_analysis(message_id=1, model="mimo-v2.6-flash", prompt_version=1,
                                      selected_images=0, result={"useful": False,
                                      "title_zh": "课程更新", "summary_zh": "合成摘要",
                                      "translation_zh": "合成中文内容", "action_required": False,
                                      "action_items": [], "important_facts": []})
                self.assertEqual(publish_pending(client, archive, state_path=state_path)["updated"], 1)
                self.assertEqual(client.records[0]["fields"]["处理状态"], "处理中")
                self.assertEqual(client.records[0]["fields"]["AI理解状态"], "已分析")
                self.assertEqual(publish_pending(client, archive, state_path=state_path)["updated"], 0)
            archive.close()


if __name__ == "__main__":
    unittest.main()
