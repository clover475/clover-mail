from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from unittest.mock import patch
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.apple_mail import SourceMessage
from clover_mail.archive import Archive
from clover_mail.mcp_server import NOTICE, ReadOnlyArchive


ANALYSIS = {
    "useful": True, "title_zh": "课程注册", "summary_zh": "请完成课程注册。",
    "translation_zh": "Register for the course.", "action_required": True,
    "action_items": [{"action": "完成注册", "deadline_iso": (date.today() + timedelta(days=3)).isoformat(), "evidence": "synthetic deadline"}],
    "important_facts": [],
}


def synthetic_message(subject: str, body: str, *, account: str = "account-01") -> SourceMessage:
    message = EmailMessage()
    message["From"] = "Teacher <teacher@example.test>"
    message["To"] = "student@example.test"
    message["Subject"] = subject
    message["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    message["Message-ID"] = f"<{subject.replace(' ', '-')}-{account}@example.test>"
    message.set_content(body)
    message.add_attachment(b"Synthetic attachment bytes", maintype="text", subtype="plain",
                           filename="agenda.txt")
    return SourceMessage(account, "INBOX", abs(hash((subject, account))) % 2**30,
                         datetime.now(timezone.utc).timestamp(), message.as_bytes())


class MCPReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "mail.sqlite3"
        archive = Archive(self.database.parent)
        for subject, account in (("Course registration", "account-01"),
                                 ("Second registration", "account-02"),
                                 ("Third registration", "account-01")):
            mid = archive.db.execute("SELECT COALESCE(MAX(id),0)+1 FROM messages").fetchone()[0]
            archive.add(synthetic_message(subject, "Register now. " + ("Long text. " * 900), account=account))
            actual_id = archive.db.execute("SELECT id FROM messages ORDER BY id DESC LIMIT 1").fetchone()[0]
            archive.save_analysis(message_id=actual_id, model="synthetic", prompt_version=1,
                                  result=ANALYSIS, selected_images=0)
            if subject == "Third registration":
                archive.set_status(actual_id, "已完成")
        archive.save_brief("2099-01-01", "Synthetic stored brief", "synthetic", {})
        archive.close()
        self.service = ReadOnlyArchive(self.database)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_accounts_use_account_id_and_truthful_unknown_metadata(self):
        result = self.service.accounts()
        self.assertEqual([a["account_id"] for a in result["accounts"]], ["account-01", "account-02"])
        self.assertEqual(result["accounts"][0]["account_type"], "unknown")
        self.assertIsNone(result["accounts"][0]["email_address"])
        self.assertEqual(result["accounts"][0]["sync_status"], "not_tracked")
        self.assertIsNone(result["accounts"][0]["last_sync_at"])

    def test_search_filters_and_paginates_and_marks_untrusted_content(self):
        first = self.service.search("registration", account_id="account-01", limit=1, offset=0)
        second = self.service.search("registration", account_id="account-01", limit=1, offset=1)
        self.assertEqual(len(first["messages"]), 1)
        self.assertTrue(first["has_more"])
        self.assertNotEqual(first["messages"][0]["message_id"], second["messages"][0]["message_id"])
        self.assertEqual(first["content_notice"], NOTICE)
        with self.assertRaises(ValueError):
            self.service.search("registration", account_id="account-0")

    def test_summary_reports_count_separately_from_bounded_recent_list(self):
        result = self.service.summary(days=7, limit=1)
        self.assertEqual(result["recent_message_count"], 3)
        self.assertEqual(len(result["recent_messages"]), 1)
        self.assertTrue(result["recent_messages_truncated"])

    def test_get_message_body_is_opt_in_and_bounded(self):
        message_id = self.service.search("registration", limit=1)["messages"][0]["message_id"]
        metadata = self.service.get_message(message_id)
        self.assertNotIn("body", metadata["message"])
        self.assertNotIn("translation_zh", metadata["message"])
        body = self.service.get_message(message_id, include_body=True, max_body_chars=80)
        self.assertEqual(len(body["message"]["body"]), 80)
        self.assertTrue(body["message"]["body_truncated"])
        self.assertNotIn("raw_message", body["message"])
        self.assertEqual(body["message"]["attachments"], [{"filename": "agenda.txt",
            "content_type": "text/plain", "size_bytes": len(b"Synthetic attachment bytes")}])
        self.assertEqual(body["message"]["translation_zh"], "Register for the course.")
        self.assertFalse(body["message"]["translation_truncated"])

    def test_pending_actions_excludes_completed_and_deadlines_use_cached_analysis(self):
        actions = self.service.pending_actions(limit=10)["messages"]
        self.assertEqual(len(actions), 2)
        self.assertTrue(all(a["handling_status"] == "未处理" for a in actions))
        summary = self.service.summary(deadline_days=7)
        self.assertTrue(summary["upcoming_deadlines"])
        self.assertEqual(summary["upcoming_deadlines"][0]["deadline"], ANALYSIS["action_items"][0]["deadline_iso"])

    def test_daily_brief_only_reads_stored_content_and_reports_absent(self):
        self.assertEqual(self.service.daily_brief("2099-01-01")["body"], "Synthetic stored brief")
        self.assertEqual(self.service.daily_brief("2099-01-02")["status"], "not_generated")

    def test_read_only_connection_rejects_writes_and_database_is_unchanged(self):
        before = self.database.read_bytes()
        with self.assertRaises(sqlite3.OperationalError):
            self.service.db.execute("DELETE FROM messages")
        self.service.db.rollback()
        self.assertEqual(self.database.read_bytes(), before)

    def test_invalid_parameters_and_missing_database_are_reported(self):
        with self.assertRaises(ValueError):
            self.service.search(" ")
        with self.assertRaises(ValueError):
            self.service.search("x", limit=101)
        with self.assertRaises(ValueError):
            self.service.get_message(1, include_body=True, max_body_chars=MAX_BODY)
        with self.assertRaises((sqlite3.Error, OSError)):
            ReadOnlyArchive(Path(self.temp.name) / "missing.sqlite3")
        with self.assertRaises(ValueError):
            self.service.summary(unread_only=True)

    def test_filter_only_search_uses_all_filters_and_time_range(self):
        day = date.today().isoformat()
        result = self.service.search("", sender="teacher@example.test", recipient="student@example.test",
                                     subject="Course", after=day, before=day)
        self.assertTrue(result["messages"])
        ids = {item["message_id"] for item in result["messages"]}
        self.assertEqual(len(ids), 1)

    def test_nonexistent_message_and_unanalyzed_message_are_safe(self):
        missing = self.service.get_message(999999)
        self.assertEqual(missing["status"], "not_found")
        self.assertEqual(self.service.summary(account="account-01")["account"], "account-01")
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            archive.add(synthetic_message("Unanalyzed fixture", "No cached analysis."))
            archive.db.commit()
            archive.close()
            reader = ReadOnlyArchive(Path(directory) / "mail.sqlite3")
            message_id = reader.search("Unanalyzed")["messages"][0]["message_id"]
            self.assertFalse(reader.get_message(message_id)["message"]["action_required"])
            self.assertEqual(reader.pending_actions()["messages"], [])
            reader.close()

    def test_empty_archive_returns_empty_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            archive.close()
            empty = ReadOnlyArchive(Path(directory) / "mail.sqlite3")
            self.assertEqual(empty.accounts()["accounts"], [])
            with self.assertRaises(ValueError):
                empty.search("")
            empty.close()

    def test_configured_account_alias_selects_exact_account_without_guessing(self):
        mapping = Path(self.temp.name) / "accounts.json"
        mapping.write_text(json.dumps({
            "account-02": {"label": "School", "email_address": "school@example.test", "account_type": "imap"},
            "account-01": {"label": "Gmail", "email_address": "gmail@example.test", "account_type": "imap"},
        }), encoding="utf-8")
        mapping.chmod(0o600)
        self.service.close()
        with patch.dict(os.environ, {"CLOVER_MAIL_ACCOUNT_MAP": str(mapping)}):
            service = ReadOnlyArchive(self.database)
            accounts = service.accounts()["accounts"]
            school = next(row for row in accounts if row["alias"] == "School")
            self.assertEqual(school["account_id"], "account-02")
            self.assertEqual(school["email_address"], "school@example.test")
            self.assertEqual({row["account_id"] for row in service.summary(account="School")["recent_messages"]},
                             {"account-02"})
            self.assertEqual(service.search("registration", account_id="School")["messages"][0]["account_alias"],
                             "School")
            service.close()
        self.service = ReadOnlyArchive(self.database)

    def test_malformed_account_map_returns_controlled_validation_error(self):
        mapping = Path(self.temp.name) / "malformed-accounts.json"
        mapping.write_text("{not json", encoding="utf-8")
        mapping.chmod(0o600)
        with patch.dict(os.environ, {"CLOVER_MAIL_ACCOUNT_MAP": str(mapping)}):
            service = ReadOnlyArchive(self.database)
            with self.assertRaisesRegex(ValueError, "account map is unreadable or invalid JSON"):
                service.accounts()
            service.close()

    def test_large_search_pagination_uses_limit_and_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory))
            for index in range(105):
                archive.add(synthetic_message(f"Batch registration {index:03d}", "Synthetic registration."))
            archive.db.commit()
            archive.close()
            reader = ReadOnlyArchive(Path(directory) / "mail.sqlite3")
            first = reader.search("registration", limit=100)
            second = reader.search("registration", limit=100, offset=100)
            self.assertEqual(len(first["messages"]), 100)
            self.assertTrue(first["has_more"])
            self.assertEqual(len(second["messages"]), 5)
            self.assertFalse(second["has_more"])
            reader.close()


MAX_BODY = 10_001


class MCPProtocolTests(unittest.TestCase):
    def test_preflight_script_reports_only_aggregate_counts_and_fails_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mail.sqlite3"
            archive = Archive(Path(directory))
            archive.add(synthetic_message("PRIVATE SUBJECT SENTINEL", "PRIVATE BODY SENTINEL", account="PRIVATE ACCOUNT SENTINEL"))
            archive.add(synthetic_message("Another private subject", "Another private body", account="second-account"))
            archive.db.commit()
            archive.close()
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [
                str(Path(__file__).parents[1] / "src"), env.get("PYTHONPATH", "")]))
            env["CLOVER_MAIL_ACCOUNT_MAP"] = str(Path(directory) / "no-account-map.json")
            script = Path(__file__).parents[1] / "scripts" / "check_mcp.py"
            completed = subprocess.run([sys.executable, str(script), "--database", str(database)],
                capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(),
                "MCP preflight passed: tools=6; accounts=2; messages=2")
            for sentinel in ("PRIVATE SUBJECT SENTINEL", "PRIVATE BODY SENTINEL",
                             "PRIVATE ACCOUNT SENTINEL", str(database)):
                self.assertNotIn(sentinel, completed.stdout + completed.stderr)

            missing = Path(directory) / "missing.sqlite3"
            failed = subprocess.run([sys.executable, str(script), "--database", str(missing)],
                capture_output=True, text=True, env=env, timeout=30)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(failed.stderr.strip(),
                "MCP preflight failed: unable to connect to or read the local archive")
            self.assertNotIn(str(missing), failed.stdout + failed.stderr)

    def test_official_sdk_stdio_discovery_and_tool_call(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Install the optional `mcp` extra to run protocol integration tests")

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mail.sqlite3"
            archive = Archive(Path(directory))
            archive.add(synthetic_message("Protocol fixture", "Synthetic protocol body."))
            archive.db.commit()
            archive.close()
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [
                str(Path(__file__).parents[1] / "src"), env.get("PYTHONPATH", "")]))
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "clover_mail.mcp_server", "--database", str(database)], env=env)

            async def exercise():
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        names = {tool.name for tool in tools.tools}
                        self.assertEqual(names, {"mail_list_accounts", "mail_get_summary", "mail_search",
                            "mail_get_message", "mail_get_pending_actions", "mail_get_daily_brief"})
                        # Clients such as Codex gate unannotated tools behind approval.
                        for tool in tools.tools:
                            self.assertTrue(tool.annotations.read_only_hint, tool.name)
                            self.assertFalse(tool.annotations.destructive_hint, tool.name)
                            self.assertFalse(tool.annotations.open_world_hint, tool.name)
                        result = await session.call_tool("mail_search", {"query": "Protocol"})
                        parsed = result.structured_content
                        if parsed is None and result.content:
                            parsed = json.loads(result.content[0].text)
                        self.assertEqual(parsed["messages"][0]["subject"], "Protocol fixture", repr(result))
                        self.assertEqual(parsed["content_notice"], NOTICE)
                        invalid = await session.call_tool("mail_search", {"query": "", "limit": 101})
                        invalid_result = invalid.structured_content
                        if invalid_result is None and invalid.content:
                            invalid_result = json.loads(invalid.content[0].text)
                        self.assertEqual(invalid_result["status"], "invalid_parameters")

            async def inaccessible_archive():
                missing_params = StdioServerParameters(command=sys.executable,
                    args=["-m", "clover_mail.mcp_server", "--database", str(Path(directory) / "missing.sqlite3")], env=env)
                async with stdio_client(missing_params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool("mail_list_accounts", {})
                        parsed = result.structured_content
                        if parsed is None and result.content:
                            parsed = json.loads(result.content[0].text)
                        self.assertEqual(parsed["status"], "unavailable")

            async def no_remote_transport():
                from clover_mail import mcp_server
                with patch.object(mcp_server, "create_server") as create_server:
                    server = create_server.return_value
                    self.assertEqual(mcp_server.main(["--database", str(database)]), 0)
                    server.run.assert_called_once_with(transport="stdio")

            asyncio.run(exercise())
            asyncio.run(inaccessible_archive())
            asyncio.run(no_remote_transport())


class ChatGPTDesktopSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.script = Path(__file__).parents[1] / "scripts" / "setup_chatgpt_desktop.py"
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env["PYTHONPATH"] = os.pathsep.join(filter(None, [
            str(Path(__file__).parents[1] / "src"), self.env.get("PYTHONPATH", "")]))

    def tearDown(self):
        self.temp.cleanup()

    def run_setup(self):
        return subprocess.run([sys.executable, str(self.script)], capture_output=True,
            text=True, env=self.env, timeout=30)

    def test_write_permission_failure_is_reported_without_local_path(self):
        spec = importlib.util.spec_from_file_location("setup_chatgpt_desktop", self.script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(Path, "mkdir", side_effect=PermissionError("/private/path")):
            with self.assertRaisesRegex(module.SetupError, "could not write plugin configuration"):
                module._atomic_json(self.home / "blocked" / "config.json", {})

    def test_setup_config_drives_real_stdio_server_and_is_idempotent(self):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.skipTest("Install the optional `mcp` extra to run plugin setup integration tests")

        archive_dir = self.home / "Library" / "Application Support" / "CloverMail"
        archive = Archive(archive_dir)
        archive.add(synthetic_message("Synthetic desktop fixture", "Never real mail."))
        archive.db.commit()
        archive.close()

        market_dir = self.home / ".agents" / "plugins"
        market_dir.mkdir(parents=True)
        unrelated = {"name": "unrelated-plugin", "source": {"source": "local", "path": "./other"}}
        marketplace_path = market_dir / "marketplace.json"
        marketplace_path.write_text(json.dumps({"name": "existing-personal", "plugins": [unrelated]}),
                                    encoding="utf-8")

        first = self.run_setup()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(str(market_dir / "clover-mail-local"), first.stdout)
        self.assertIn(str(marketplace_path), first.stdout)
        plugin_dir = market_dir / "clover-mail-local"
        plugin = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
        config = json.loads((plugin_dir / "mcp.json").read_text(encoding="utf-8"))
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "clover-mail-local")
        self.assertIn("Read-only", plugin["description"])
        interface = plugin["extensions"]["com.openai"]["interface"]
        self.assertEqual(interface["displayName"], "Clover Mail Local")
        self.assertEqual(interface["capabilities"], ["Read"])
        self.assertEqual(config["mcpServers"]["clover-mail-local"], {
            "type": "stdio", "command": os.path.normpath(str(Path(sys.executable).absolute())),
            "args": ["-m", "clover_mail.mcp_server"],
        })
        self.assertEqual(marketplace["name"], "existing-personal")
        self.assertIn(unrelated, marketplace["plugins"])
        self.assertEqual(sum(p.get("name") == "clover-mail-local" for p in marketplace["plugins"]), 1)
        entry = next(p for p in marketplace["plugins"] if p.get("name") == "clover-mail-local")
        self.assertEqual(entry["source"]["path"], "./.agents/plugins/clover-mail-local")
        self.assertEqual((self.home / entry["source"]["path"]).resolve(), plugin_dir.resolve())
        serialized = json.dumps([plugin, config, marketplace])
        self.assertNotIn("mail.sqlite3", serialized)
        self.assertNotIn("account-01", serialized)
        self.assertNotIn("credentials", serialized.casefold())

        second = self.run_setup()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(marketplace_path.read_text(encoding="utf-8")), marketplace)

        params = StdioServerParameters(command=config["mcpServers"]["clover-mail-local"]["command"],
            args=config["mcpServers"]["clover-mail-local"]["args"], env=self.env)

        async def exercise_generated_command():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(len(tools.tools), 6)
                    result = await session.call_tool("mail_list_accounts", {})
                    parsed = result.structured_content
                    if parsed is None and result.content:
                        parsed = json.loads(result.content[0].text)
                    self.assertEqual(parsed["accounts"][0]["message_count"], 1)

        asyncio.run(exercise_generated_command())

    def test_conflicting_or_malformed_marketplace_is_never_overwritten(self):
        market_dir = self.home / ".agents" / "plugins"
        market_dir.mkdir(parents=True)
        marketplace_path = market_dir / "marketplace.json"
        conflicting = {"name": "clover-mail-local", "source": {"source": "local", "path": "./someone-else"}}
        original = {"name": "personal", "plugins": [conflicting]}
        marketplace_path.write_text(json.dumps(original), encoding="utf-8")
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts", result.stderr)
        self.assertEqual(json.loads(marketplace_path.read_text(encoding="utf-8")), original)
        self.assertFalse((market_dir / "clover-mail-local").exists())

        marketplace_path.write_text("{invalid", encoding="utf-8")
        malformed = marketplace_path.read_bytes()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marketplace_path.read_bytes(), malformed)
        self.assertFalse((market_dir / "clover-mail-local").exists())


if __name__ == "__main__":
    unittest.main()
