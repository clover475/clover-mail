"""One idempotent pass for launchd or manual operation."""

from __future__ import annotations

import fcntl
import os
from datetime import datetime, timedelta

from .archive import Archive, DEFAULT_DATA_DIR
from .brief import generate, send_to_email, send_to_feishu, today_local
from .email_delivery import SMTPConfig
from .feishu import FeishuClient, FeishuConfig
from .mail_center import publish_pending, pull_statuses
from .ai import AIConfig
from .processing import PROMPT_VERSION, analyze_pending
from .notion import NotionClient, NotionConfig, NotionError, notion_configured, publish_pending as publish_notion
from .timezone import local_zone


def _publish_notion_if_configured(archive: Archive, result: dict) -> None:
    if not notion_configured():
        return
    try:
        result["notion_publish"] = publish_notion(
            NotionClient(NotionConfig.from_environment()), archive, limit=20)
    except NotionError as exc:
        # A secondary mirror must not suppress the established Feishu and
        # Gmail brief path. Its failed rows remain pending for the next pass.
        result["notion_publish"] = {"error": str(exc)}


def run_once(*, sync_fn, analysis_limit: int = 3, force_brief: bool = False) -> dict:
    return _locked_run(lambda: _run_unlocked(sync_fn=sync_fn, analysis_limit=analysis_limit,
                                              force_brief=force_brief))


def run_local_once(*, sync_fn, analysis_limit: int = 2) -> dict:
    """Keep Mail Center current while Gmail delivery is being configured."""
    return _locked_run(lambda: _run_local_unlocked(sync_fn=sync_fn, analysis_limit=analysis_limit))


def _locked_run(action) -> dict:
    DEFAULT_DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = DEFAULT_DATA_DIR / "runner.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"already_running": True}
        return action()
    finally:
        os.close(lock_fd)


def _run_local_unlocked(*, sync_fn, analysis_limit: int) -> dict:
    result: dict = {"sync": sync_fn(None)}
    if result["sync"]["errors"]:
        return result
    archive = Archive()
    try:
        model = AIConfig.from_environment()
        result["analysis"] = analyze_pending(archive, config=model, limit=analysis_limit)
        feishu = FeishuClient(FeishuConfig.from_environment())
        result["feishu_publish"] = publish_pending(feishu, archive)
        result["status"] = pull_statuses(feishu, archive)
        _publish_notion_if_configured(archive, result)
        return result
    finally:
        archive.close()


def _run_unlocked(*, sync_fn, analysis_limit: int, force_brief: bool) -> dict:
    result: dict = {"sync": sync_fn(None)}
    if result["sync"]["errors"]:
        return result
    archive = Archive()
    try:
        model = AIConfig.from_environment()
        result["analysis"] = analyze_pending(archive, config=model, limit=analysis_limit)
        feishu = FeishuClient(FeishuConfig.from_environment())
        result["feishu_publish"] = publish_pending(feishu, archive)
        result["status"] = pull_statuses(feishu, archive)
        _publish_notion_if_configured(archive, result)
        now = datetime.now(local_zone())
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        previous = archive.get_brief(yesterday)
        previous_delivered = bool(previous and previous["feishu_sent_at"] and
                                  previous["email_sent_at"])
        if not previous_delivered and (previous or archive.brief_items(yesterday)):
            pending_previous = archive.pending_analysis_for_day(
                day=yesterday, model=model.storage_model, prompt_version=PROMPT_VERSION,
                include_deferred=False,
            )
            if pending_previous:
                result["brief_catchup"] = {"deferred":
                                            f"yesterday's mail awaits analysis: {pending_previous}"}
            else:
                result["brief_catchup"] = generate(
                    archive, day=yesterday, config=model, finalize_early=True)
                result["brief_catchup_feishu"] = send_to_feishu(archive, feishu,
                                                                  day=yesterday)
                result["brief_catchup_email"] = send_to_email(
                    archive, SMTPConfig.from_environment(), day=yesterday)
        if not force_brief and now.hour < 21:
            return result
        day = today_local()
        pending_today = archive.pending_analysis_for_day(
            day=day, model=model.storage_model, prompt_version=PROMPT_VERSION,
            include_deferred=False,
        )
        if pending_today:
            result["brief"] = {"deferred": f"today's mail awaits analysis: {pending_today}"}
            return result
        result["brief"] = generate(archive, day=day, config=model,
                                   finalize_early=now.hour >= 21)
        result["brief_feishu"] = send_to_feishu(archive, feishu, day=day)
        result["brief_email"] = send_to_email(archive, SMTPConfig.from_environment(), day=day)
        return result
    finally:
        archive.close()
