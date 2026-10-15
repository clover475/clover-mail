from __future__ import annotations

import sys
import fcntl
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.runner import run_local_once, run_once
from clover_mail.automation import launch_agent_definition


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.data_patch = patch("clover_mail.runner.DEFAULT_DATA_DIR", Path(self.directory.name))
        self.data_patch.start()

    def tearDown(self):
        self.data_patch.stop()
        self.directory.cleanup()

    def test_local_schedule_uses_local_command(self):
        agent = launch_agent_definition(project_root=Path("/project"), python_path=Path("/python"),
                                        env_file=Path("/private.env"), local_only=True)
        self.assertEqual(agent["ProgramArguments"][-1], "run-local-once")
        self.assertEqual(agent["StartInterval"], 900)

    def test_local_pass_updates_feishu_without_email(self):
        archive = MagicMock()
        with patch("clover_mail.runner.Archive", return_value=archive), \
             patch("clover_mail.runner.AIConfig.from_environment", return_value=MagicMock(model="mimo-v2.6-flash")), \
             patch("clover_mail.runner.analyze_pending", return_value={"errors": [], "analyzed": 2}), \
             patch("clover_mail.runner.FeishuConfig.from_environment"), \
             patch("clover_mail.runner.FeishuClient") as feishu, \
             patch("clover_mail.runner.publish_pending", return_value={"published": 1}) as publish, \
             patch("clover_mail.runner.pull_statuses", return_value={"statuses_read": 1}) as pull, \
             patch("clover_mail.runner.send_to_email") as email:
            result = run_local_once(sync_fn=lambda _: {"errors": 0})
        self.assertEqual(result["analysis"]["analyzed"], 2)
        self.assertEqual(result["feishu_publish"]["published"], 1)
        feishu.assert_called_once()
        publish.assert_called_once()
        pull.assert_called_once()
        email.assert_not_called()

    def test_concurrent_run_is_skipped_before_sync(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("clover_mail.runner.DEFAULT_DATA_DIR", Path(directory)):
            descriptor = os.open(Path(directory) / "runner.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                sync = MagicMock()
                self.assertEqual(run_once(sync_fn=sync), {"already_running": True})
                sync.assert_not_called()
            finally:
                os.close(descriptor)

    def test_one_analysis_failure_does_not_hide_successful_mail(self):
        archive = MagicMock()
        archive.pending_analysis_for_day.return_value = 1
        with patch("clover_mail.runner.Archive", return_value=archive), \
             patch("clover_mail.runner.AIConfig.from_environment", return_value=MagicMock(model="mimo-v2.6-flash")), \
             patch("clover_mail.runner.analyze_pending", return_value={"errors": ["message 2 failed"], "analyzed": 1}), \
             patch("clover_mail.runner.FeishuConfig.from_environment"), \
             patch("clover_mail.runner.FeishuClient"), \
             patch("clover_mail.runner.publish_pending", return_value={"published": 1}) as publish, \
             patch("clover_mail.runner.pull_statuses", return_value={"statuses_read": 1}):
            result = run_once(sync_fn=lambda _: {"errors": 0}, force_brief=True)
        publish.assert_called_once()
        self.assertEqual(result["feishu_publish"]["published"], 1)
        self.assertEqual(result["brief"], {"deferred": "today's mail awaits analysis: 1"})

    def test_uncached_source_warning_does_not_block_analyzed_mail(self):
        archive = MagicMock()
        archive.pending_analysis_for_day.return_value = 1
        with patch("clover_mail.runner.Archive", return_value=archive), \
             patch("clover_mail.runner.AIConfig.from_environment", return_value=MagicMock(model="mimo-v2.6-flash")), \
             patch("clover_mail.runner.analyze_pending", return_value={"errors": [], "analyzed": 1}), \
             patch("clover_mail.runner.FeishuConfig.from_environment"), \
             patch("clover_mail.runner.FeishuClient"), \
             patch("clover_mail.runner.publish_pending", return_value={"published": 1}) as publish, \
             patch("clover_mail.runner.pull_statuses", return_value={"statuses_read": 1}):
            result = run_once(sync_fn=lambda _: {"errors": 0, "source_warnings": 2}, force_brief=True)
        publish.assert_called_once()
        self.assertEqual(result["sync"]["source_warnings"], 2)
        self.assertEqual(result["feishu_publish"]["published"], 1)
        self.assertEqual(result["brief"], {"deferred": "today's mail awaits analysis: 1"})
        archive.close.assert_called_once()

    def test_morning_run_catches_up_yesterdays_unsent_brief(self):
        archive = MagicMock()
        archive.get_brief.return_value = {"feishu_sent_at": None, "email_sent_at": None}
        archive.pending_analysis_for_day.return_value = 0
        with patch("clover_mail.runner.Archive", return_value=archive), \
             patch("clover_mail.runner.datetime") as clock, \
             patch("clover_mail.runner.AIConfig.from_environment", return_value=MagicMock(model="mimo-v2.6-flash")), \
             patch("clover_mail.runner.analyze_pending", return_value={"errors": [], "analyzed": 0}), \
             patch("clover_mail.runner.FeishuConfig.from_environment"), \
             patch("clover_mail.runner.FeishuClient"), \
             patch("clover_mail.runner.publish_pending", return_value={"published": 0}), \
             patch("clover_mail.runner.pull_statuses", return_value={"statuses_read": 0}), \
             patch("clover_mail.runner.generate", return_value={"created": True}) as generate, \
             patch("clover_mail.runner.send_to_feishu", return_value={"sent": True}) as send_feishu, \
             patch("clover_mail.runner.SMTPConfig.from_environment"), \
             patch("clover_mail.runner.send_to_email", return_value={"sent": True}) as send_email:
            clock.now.return_value = datetime(2026, 10, 9, 0, 0,
                                              tzinfo=ZoneInfo("UTC"))
            result = run_once(sync_fn=lambda _: {"errors": 0})
        self.assertEqual(result["brief_catchup"], {"created": True})
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["day"], "2026-10-08")
        self.assertTrue(generate.call_args.kwargs["finalize_early"])
        send_feishu.assert_called_once()
        send_email.assert_called_once()


if __name__ == "__main__":
    unittest.main()
