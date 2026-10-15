"""Create a daily decision brief from already analyzed mail."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime

from .archive import Archive
from .feishu import FeishuClient
from .ai import AIConfig, _request
from .email_delivery import SMTPConfig, send_brief
from .timezone import local_zone

BRIEF_INSTRUCTIONS = """请依据下面的结构化邮件事实写一份中文邮件日报。先写今天的判断和优先事项，再写明确待办与截止日期，最后写值得关注的资讯。不要逐封堆砌摘要。不得补充未提供的事实、日期或建议。只输出给用户看的日报正文。"""


def today_local() -> str:
    return datetime.now(local_zone()).date().isoformat()


def preview(archive: Archive, day: str) -> dict:
    items = archive.brief_items(day)
    actions = [action for item in items for action in item["action_items"] if item["status"] not in {"已完成", "忽略"}]
    deadlines = [action for action in actions if action.get("deadline_iso")]
    return {
        "day": day,
        "valuable_mail": len(items),
        "requires_action": sum(item["action_required"] and item["status"] not in {"已完成", "忽略"} for item in items),
        "new_action_items": len(actions),
        "deadline_items": len(deadlines),
        "items": items,
    }


def generate(archive: Archive, *, day: str, config: AIConfig,
             finalize_early: bool = False) -> dict:
    from .processing import PROMPT_VERSION

    facts = preview(archive, day)
    facts["unprocessed_mail"] = archive.pending_analysis_for_day(
        day=day, model=config.storage_model, prompt_version=PROMPT_VERSION,
    )
    payload = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    source_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    existing = archive.get_brief(day)
    if existing:
        if existing["usage"].get("_source_sha256") == source_digest:
            return {"day": day, "created": False, "usage": existing["usage"]}
        if existing["feishu_sent_at"] or existing["email_sent_at"]:
            # An early live validation may have been sent before today's mail
            # finished arriving. The evening pass can replace it once; a Brief
            # first generated at or after 21:00 remains immutable.
            generated = datetime.fromisoformat(existing["generated_at"]).astimezone(
                local_zone())
            if not (finalize_early and generated.date().isoformat() == day and
                    generated.hour < 21):
                return {"day": day, "created": False, "usage": existing["usage"]}
    if not facts["items"]:
        body = f"# {day} 邮件日报\n\n今天暂无已完成分析的有价值邮件。"
        usage: dict = {}
    else:
        synthesis, usage = _request(config, payload, system_prompt=BRIEF_INSTRUCTIONS)
        body = f"# {day} 邮件日报\n\n有价值邮件 {facts['valuable_mail']} 封；待处理邮件 {facts['requires_action']} 封；新增待办 {facts['new_action_items']} 项；含截止日期的待办 {facts['deadline_items']} 项。\n\n{synthesis}"
    if facts["unprocessed_mail"]:
        body += f"\n\n注意：另有 {facts['unprocessed_mail']} 封当天邮件尚未完成 AI 分析，本日报未覆盖其内容。"
    unseen_image_mail = sum(bool(item.get("remote_images_not_loaded")) for item in facts["items"])
    if unseen_image_mail:
        body += f"\n\n图片覆盖提示：{unseen_image_mail} 封邮件含未加载的远程图片，图片中的信息可能未纳入本日报。"
    usage = {**usage, "_source_sha256": source_digest}
    archive.save_brief(day, body, config.storage_model, usage)
    return {"day": day, "created": True, "usage": usage}


def send_to_feishu(archive: Archive, client: FeishuClient, *, day: str) -> dict:
    brief = archive.get_brief(day)
    if not brief:
        raise ValueError("brief has not been generated")
    if brief["feishu_sent_at"]:
        return {"sent": False, "already_sent": True}
    if not client.config.open_id:
        raise ValueError("FEISHU_OPEN_ID is required for a private daily brief")
    client.send_text(brief["body"], open_id=client.config.open_id)
    archive.mark_brief_sent(day, "feishu")
    return {"sent": True, "already_sent": False}


def send_to_email(archive: Archive, config: SMTPConfig, *, day: str) -> dict:
    brief = archive.get_brief(day)
    if not brief:
        raise ValueError("brief has not been generated")
    if brief["email_sent_at"]:
        return {"sent": False, "already_sent": True}
    send_brief(day=day, body=brief["body"], config=config)
    archive.mark_brief_sent(day, "email")
    return {"sent": True, "already_sent": False}
