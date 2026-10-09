from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

from . import __version__
from .apple_mail import MailSourceError, discover_mail_root, doctor, iter_messages
from .archive import Archive
from .ai import DEFAULT_MIMO_BASE_URL, DEFAULT_MIMO_MODEL, AIConfig, AIError
from .processing import analyze_pending
from .feishu import FeishuClient, FeishuConfig, FeishuError
from .mail_center import provision, publish_pending, pull_statuses
from .brief import generate as generate_brief, preview as preview_brief, send_to_email, send_to_feishu, today_local
from .email_delivery import DeliveryError, SMTPConfig
from .runtime import load_runtime_env
from .runner import run_local_once, run_once
from .automation import install_launch_agent, install_local_launch_agent
from .query import ask
from .notion import NotionClient, NotionConfig, NotionError, publish_pending as publish_notion


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clover-mail", description="Local, read-only Apple Mail ingestion")
    parser.add_argument("--version", action="version", version=f"clover-mail {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check local Apple Mail data access without showing message content")
    sync = commands.add_parser("sync", help="import cached messages into the private local archive")
    sync.add_argument("--days", type=int, default=None, help="rescan this many days (first run defaults to 30)")
    sync.add_argument("--json", action="store_true", help="print machine-readable counts only")
    prepare = commands.add_parser("prepare", help="inspect a small pending analysis batch without sending mail")
    prepare.add_argument("--limit", type=int, default=5)
    analyze = commands.add_parser("analyze", help="analyze a small pending batch with the configured AI provider")
    analyze.add_argument("--limit", type=int, default=5)
    commands.add_parser("feishu-setup", help="create the Mail Center Base and its mail table")
    publish = commands.add_parser("feishu-publish", help="publish all archived mail to Feishu")
    publish.add_argument("--limit", type=int, default=20)
    commands.add_parser("feishu-pull-status", help="read handling states edited in Feishu")
    notion = commands.add_parser("notion-publish", help="mirror local mail text and translations to Notion")
    notion.add_argument("--limit", type=int, default=20)
    commands.add_parser("notion-doctor", help="verify the configured Notion data source")
    search = commands.add_parser("search", help="search the local mail archive")
    search.add_argument("text", help="word or phrase to find in original and Chinese content")
    search.add_argument("--days", type=int, default=None)
    search.add_argument("--limit", type=int, default=20)
    natural = commands.add_parser("ask", help="interpret a natural-language query with the configured model and search locally")
    natural.add_argument("question")
    natural.add_argument("--limit", type=int, default=20)
    for name, help_text in (
        ("brief-preview", "show daily counts without a model call"),
        ("brief-generate", "generate and store an AI daily brief"),
        ("brief-show", "show a stored daily brief"),
        ("brief-send-feishu", "send an unsent brief to Feishu"),
        ("brief-send-email", "send an unsent brief by SMTP"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--day", default=None, help="day in YYYY-MM-DD, default today in configured timezone")
    scheduled = commands.add_parser("run-once", help="sync, analyze, publish and send due briefs once")
    scheduled.add_argument("--analysis-limit", type=int, default=3)
    scheduled.add_argument("--force-brief", action="store_true")
    local = commands.add_parser("run-local-once", help="sync, analyze and update Feishu before Gmail is configured")
    local.add_argument("--analysis-limit", type=int, default=2)
    commands.add_parser("install-launch-agent", help="schedule run-once after live readiness checks")
    commands.add_parser("install-local-launch-agent", help="schedule sync, AI and Feishu without Gmail")
    return parser


def run_sync(days: int | None) -> dict[str, object]:
    root = discover_mail_root()
    archive = Archive()
    try:
        last_success = archive.latest_success()
        if days is not None:
            since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        elif last_success is None:
            since = datetime.now(timezone.utc) - timedelta(days=30)
        else:
            # Small overlap covers indexing delay and clock skew; uniqueness handles repeats.
            since = last_success - timedelta(days=3)
        messages, errors = iter_messages(root, since=since)
        # The Gmail recipient may also be an Apple Mail account. Keep our own
        # delivered Brief out of the archive so it cannot feed the next Brief.
        own_briefs = 0
        imported = 0
        for message in messages:
            message_id = str(BytesParser(policy=policy.default)
                             .parsebytes(message.raw_message, headersonly=True)
                             .get("Message-ID", "")).strip().casefold()
            if message_id.startswith("<clover-mail-brief-") and message_id.endswith("@local.clover-mail>"):
                own_briefs += 1
                continue
            imported += archive.add(message)
        skipped = len(messages) - imported - own_briefs
        archive.complete_run(imported=imported, skipped=skipped, errors=len(errors))
        result = {
            "since": since.isoformat(),
            "imported": imported,
            "already_present": skipped,
            "own_briefs_ignored": own_briefs,
            "source_warnings": len(errors),
            "errors": 0,
            "archive": str(archive.path),
            "archive_total": archive.count(),
            "read_only_source": True,
        }
        if errors:
            result["warning_examples"] = errors[:10]
        return result
    finally:
        archive.close()


def main(argv: list[str] | None = None) -> int:
    try:
        load_runtime_env()
        args = build_parser().parse_args(argv)
        if args.command == "run-once":
            result = run_once(sync_fn=run_sync, analysis_limit=args.analysis_limit, force_brief=args.force_brief)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result.get("sync", {}).get("errors") or result.get("analysis", {}).get("errors") else 0
        if args.command == "run-local-once":
            result = run_local_once(sync_fn=run_sync, analysis_limit=args.analysis_limit)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result.get("sync", {}).get("errors") or result.get("analysis", {}).get("errors") else 0
        if args.command == "install-launch-agent":
            print(str(install_launch_agent()))
            return 0
        if args.command == "install-local-launch-agent":
            print(str(install_local_launch_agent()))
            return 0
        if args.command == "doctor":
            result = doctor()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"Ready: {result['accounts']} accounts, {result['mailboxes']} mailboxes, {result['indexed_messages']} indexed messages.")
            return 0
        if args.command in {"prepare", "analyze"}:
            if not 1 <= args.limit <= 100:
                raise ValueError("--limit must be between 1 and 100")
            config = AIConfig.from_environment() if args.command == "analyze" else AIConfig(
                api_key="unused",
                base_url=os.environ.get("CLOVER_MAIL_AI_BASE_URL", DEFAULT_MIMO_BASE_URL),
                model=os.environ.get("CLOVER_MAIL_AI_MODEL", os.environ.get("MIMO_MODEL", DEFAULT_MIMO_MODEL)),
                provider=os.environ.get("CLOVER_MAIL_AI_PROVIDER", "mimo"),
            )
            archive = Archive()
            try:
                result = analyze_pending(archive, config=config, limit=args.limit, dry_run=args.command == "prepare")
            finally:
                archive.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["errors"] else 0
        if args.command in {"feishu-setup", "feishu-publish", "feishu-pull-status"}:
            client = FeishuClient(FeishuConfig.from_environment())
            if args.command == "feishu-setup":
                state = provision(client)
                print(json.dumps({"app_token": state["app_token"], "table_id": state["table_id"],
                                  "url": state.get("url", "")}, ensure_ascii=False, indent=2))
                return 0
            archive = Archive()
            try:
                if args.command == "feishu-publish":
                    if not 1 <= args.limit <= 100:
                        raise ValueError("--limit must be between 1 and 100")
                    result = publish_pending(client, archive, limit=args.limit)
                else:
                    result = pull_statuses(client, archive)
            finally:
                archive.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"notion-doctor", "notion-publish"}:
            client = NotionClient(NotionConfig.from_environment())
            if args.command == "notion-doctor":
                source = client.get_data_source()
                names = set((source.get("properties") or {}).keys())
                required = {"中文标题", "本地邮件ID", "来源邮箱", "发件人", "中文摘要",
                            "待办", "处理状态", "AI理解状态", "原文标题", "邮箱文件夹",
                            "收件日期", "截止日期"}
                if not required <= names:
                    raise NotionError("Notion Mail Center is missing required fields")
                print(json.dumps({"ready": True, "data_source_id": client.config.data_source_id},
                                 ensure_ascii=False, indent=2))
                return 0
            archive = Archive()
            try:
                result = publish_notion(client, archive, limit=args.limit)
            finally:
                archive.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "search":
            if args.days is not None and args.days < 1:
                raise ValueError("--days must be at least 1")
            archive = Archive()
            try:
                result = archive.search(args.text, days=args.days, limit=args.limit)
            finally:
                archive.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "ask":
            archive = Archive()
            try:
                result = ask(archive, args.question, config=AIConfig.from_environment(), limit=args.limit)
            finally:
                archive.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command.startswith("brief-"):
            day = args.day or today_local()
            date.fromisoformat(day)
            archive = Archive()
            try:
                if args.command == "brief-preview":
                    facts = preview_brief(archive, day)
                    result = {key: value for key, value in facts.items() if key != "items"}
                elif args.command == "brief-generate":
                    result = generate_brief(archive, day=day, config=AIConfig.from_environment())
                elif args.command == "brief-show":
                    saved = archive.get_brief(day)
                    if not saved:
                        raise ValueError("brief has not been generated")
                    print(saved["body"])
                    return 0
                elif args.command == "brief-send-feishu":
                    result = send_to_feishu(archive, FeishuClient(FeishuConfig.from_environment()), day=day)
                else:
                    result = send_to_email(archive, SMTPConfig.from_environment(), day=day)
            finally:
                archive.close()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.days is not None and args.days < 1:
            raise ValueError("--days must be at least 1")
        result = run_sync(args.days)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"Imported {result['imported']}; already present {result['already_present']}; "
                f"source warnings {result['source_warnings']}; local total {result['archive_total']}."
            )
            print(f"Private archive: {result['archive']}")
            if result["source_warnings"]:
                for message in result.get("warning_examples", []):
                    print(f"- {message}", file=sys.stderr)
        return 1 if result["errors"] else 0
    except (MailSourceError, AIError, FeishuError, NotionError, DeliveryError, OSError, ValueError) as exc:
        print(f"clover-mail: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
