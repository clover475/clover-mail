from __future__ import annotations

import sys
import tempfile
import unittest
import fcntl
import os
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.apple_mail import SourceMessage
from clover_mail.archive import Archive
from clover_mail.notion import _page_content, publish_pending


class FakeNotion:
    def __init__(self) -> None:
        self.pages = {}

    def find_page(self, mail_id: int) -> str | None:
        return next((page_id for page_id, page in self.pages.items()
                     if page["mail_id"] == str(mail_id)), None)

    def create_page(self, properties: dict, blocks: list[dict]) -> str:
        page_id = f"page-{len(self.pages) + 1}"
        self.pages[page_id] = {"mail_id": properties["本地邮件ID"]["rich_text"][0]["text"]["content"],
                               "properties": properties, "blocks": blocks}
        return page_id

    def update_page(self, page_id: str, properties: dict) -> None:
        self.pages[page_id]["properties"] = properties

    def replace_blocks(self, page_id: str, blocks: list[dict]) -> None:
        self.pages[page_id]["blocks"] = blocks


def _section(blocks: list[dict], heading: str) -> str:
    index = next(i for i, block in enumerate(blocks) if block["type"] == "heading_2" and
                 block["heading_2"]["rich_text"][0]["text"]["content"] == heading)
    parts = []
    for block in blocks[index + 1:]:
        if block["type"] == "heading_2":
            break
        parts.append(block["paragraph"]["rich_text"][0]["text"]["content"])
    return "".join(parts)


class NotionMirrorTests(unittest.TestCase):
    def test_concurrent_publisher_skips_without_creating_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "archive")
            message = EmailMessage()
            message["From"] = "News <news@example.org>"
            message["Subject"] = "Update"
            message.set_content("Synthetic text")
            archive.add(SourceMessage("gmail", "INBOX", 4, None, message.as_bytes()))
            lock_fd = os.open(archive.directory / "notion.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(publish_pending(FakeNotion(), archive), {"busy": True})
            finally:
                os.close(lock_fd)
            archive.close()

    def test_full_original_then_full_translation_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "archive")
            original = "Newsletter poster text and registration details. " * 150
            translation = "中文翻译和海报信息。" * 500
            message = EmailMessage()
            message["From"] = "School <school@example.edu>"
            message["Subject"] = "AI Event"
            message["Message-ID"] = "<notion-test@example.edu>"
            message.set_content(original)
            archive.add(SourceMessage("outlook", "Inbox", 3, None, message.as_bytes()))
            client = FakeNotion()
            with patch("clover_mail.notion.account_labels", return_value={"outlook": "Outlook"}):
                self.assertEqual(publish_pending(client, archive)["created"], 1)
                page = client.pages["page-1"]
                self.assertEqual(_section(page["blocks"], "邮件原文"), original.strip())
                self.assertEqual(page["properties"]["AI理解状态"]["select"]["name"], "待分析")
                self.assertEqual(publish_pending(client, archive)["created"], 0)
                archive.save_analysis(message_id=1, model="mimo-v2.6-flash", prompt_version=1,
                                      selected_images=0, result={
                                          "useful": True, "title_zh": "人工智能活动", "summary_zh": "合成摘要",
                                          "translation_zh": translation, "action_required": True,
                                          "action_items": [{"action": "报名活动", "deadline_iso": "2026-10-20"}],
                                          "important_facts": ["海报有报名说明"]})
                self.assertEqual(publish_pending(client, archive)["updated"], 1)
                page = client.pages["page-1"]
                self.assertEqual(_section(page["blocks"], "完整中文翻译"), translation)
                self.assertEqual(_section(page["blocks"], "邮件原文"), original.strip())
                self.assertEqual(page["properties"]["截止日期"]["date"]["start"], "2026-10-20")
                archive.set_status(1, "已完成")
                self.assertEqual(publish_pending(client, archive)["updated"], 1)
                self.assertEqual(client.pages["page-1"]["properties"]["处理状态"]["select"]["name"], "已完成")
                self.assertEqual(len(client.pages), 1)
            archive.close()

    def test_reconcile_remote_page_after_local_checkpoint_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "archive")
            message = EmailMessage()
            message["From"] = "News <news@example.org>"
            message["Subject"] = "Update"
            message.set_content("Synthetic text")
            archive.add(SourceMessage("gmail", "INBOX", 4, None, message.as_bytes()))
            client = FakeNotion()
            row = archive.pending_notion()[0]
            properties, blocks = _page_content(row, {"gmail": "Gmail"})
            client.create_page(properties, blocks)
            with patch("clover_mail.notion.account_labels", return_value={"gmail": "Gmail"}):
                result = publish_pending(client, archive)
            self.assertEqual(result, {"created": 0, "updated": 1, "reconciled": 1})
            self.assertEqual(len(client.pages), 1)
            archive.close()


if __name__ == "__main__":
    unittest.main()
