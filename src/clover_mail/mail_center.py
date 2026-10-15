"""Provision and synchronize a simple Feishu Base Mail Center."""

from __future__ import annotations

import json
from pathlib import Path

from .apple_mail import account_labels, parsedate_utc
from .archive import Archive, DEFAULT_DATA_DIR
from .content import extract_content
from .feishu import FeishuClient, FeishuError

STATE_PATH = DEFAULT_DATA_DIR / "feishu-center.json"
STATUSES = ("未处理", "处理中", "已完成", "忽略")
SCHEMA_VERSION = 3
ORIGINAL_TEXT_LIMIT = 20_000
MAIL_FIELDS = [
    {"field_name": "中文标题", "type": 1},
    {"field_name": "收件时间", "type": 1},
    {"field_name": "收件日期", "type": 5, "property": {"date_formatter": "yyyy-MM-dd HH:mm", "auto_fill": False}},
    {"field_name": "发件人", "type": 1},
    {"field_name": "中文摘要", "type": 1},
    {"field_name": "完整中文内容", "type": 1},
    {"field_name": "需要处理", "type": 7},
    {"field_name": "待办", "type": 1},
    {"field_name": "截止日期", "type": 1},
    {"field_name": "图片说明", "type": 1},
    {"field_name": "处理状态", "type": 3, "property": {"options": [{"name": name} for name in STATUSES]}},
    {"field_name": "原文标题", "type": 1},
    {"field_name": "本地邮件ID", "type": 1},
    {"field_name": "来源邮箱", "type": 3, "property": {"options": [
        {"name": name} for name in ("Outlook", "Gmail", "163 Mail", "Other")]}},
    {"field_name": "邮箱文件夹", "type": 1},
    {"field_name": "AI理解状态", "type": 3, "property": {"options": [
        {"name": "待分析"}, {"name": "已分析"}]}},
    {"field_name": "原文内容", "type": 1},
]


def _load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise FeishuError("invalid local Mail Center state")
    return data


def _save_state(data: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.chmod(0o600)
    temp.replace(path)


def provision(client: FeishuClient, *, state_path: Path = STATE_PATH) -> dict:
    state = _load_state(state_path)
    if not state.get("app_token"):
        response = client.create_base("Clover Mail Center")
        app = response.get("app", response)
        state["app_token"] = app.get("app_token")
        state["url"] = app.get("url", "")
        if not state["app_token"]:
            raise FeishuError("Feishu created a Base without returning its app token")
        _save_state(state, state_path)
    if not state.get("table_id"):
        response = client.create_table(state["app_token"], "邮件", MAIL_FIELDS)
        table = response.get("table", response)
        state["table_id"] = table.get("table_id")
        if not state["table_id"]:
            raise FeishuError("Feishu created a table without returning its table ID")
        _save_state(state, state_path)
    app_token, table_id = state["app_token"], state["table_id"]
    fields = client.list_fields(app_token, table_id).get("items") or []
    by_name = {field.get("field_name"): field for field in fields}
    added = False
    for definition in MAIL_FIELDS:
        name = definition["field_name"]
        if name not in by_name:
            created = client.create_field(app_token, table_id, definition)
            by_name[name] = created.get("field", created)
            added = True
    if added:
        by_name = {field.get("field_name"): field for field in
                   (client.list_fields(app_token, table_id).get("items") or [])}
    date_field = by_name["收件日期"]
    field_id = date_field.get("field_id")
    if not field_id:
        raise FeishuError("Mail Center date field has no ID")
    views = client.list_views(app_token, table_id).get("items") or []
    today = next((view for view in views if view.get("view_name") == "今天"), None)
    if today is None:
        created = client.create_view(app_token, table_id, "今天")
        today = created.get("view", created)
    view_id = today.get("view_id")
    if not view_id:
        raise FeishuError("Mail Center today view has no ID")
    client.update_view(app_token, table_id, view_id, {
        "filter_info": {"conjunction": "and", "conditions": [
            {"field_id": field_id, "operator": "is", "value": '["Today"]'}
        ]}
    })
    source_field_id = by_name["来源邮箱"].get("field_id")
    if source_field_id:
        option_ids = {option.get("name"): option.get("id")
                      for option in by_name["来源邮箱"].get("property", {}).get("options", [])}
        for label in ("Outlook", "Gmail"):
            option_id = option_ids.get(label)
            if not option_id:
                raise FeishuError(f"Mail Center source option has no ID: {label}")
            view = next((item for item in views if item.get("view_name") == label), None)
            if view is None:
                created = client.create_view(app_token, table_id, label)
                view = created.get("view", created)
            client.update_view(app_token, table_id, view["view_id"], {
                "filter_info": {"conjunction": "and", "conditions": [
                    {"field_id": source_field_id, "operator": "is",
                     "value": json.dumps([option_id])}
                ]}
            })
            state["outlook_view_id" if label == "Outlook" else "gmail_view_id"] = view["view_id"]
    # A new Base also contains a blank default table. Lead users to today's
    # mail view; keep the all-mail URL for historical browsing.
    base_url = state.get("url", "").split("?", 1)[0]
    if base_url:
        state["all_url"] = f"{base_url}?table={table_id}"
        state["url"] = f"{state['all_url']}&view={view_id}"
    state["today_view_id"] = view_id
    _save_state(state, state_path)
    return state


def _all_records(client: FeishuClient, state: dict,
                 fields: tuple[str, ...] = ("本地邮件ID", "处理状态")) -> list[dict]:
    records: list[dict] = []
    page_token = ""
    while True:
        response = client.list_records(state["app_token"], state["table_id"], page_token,
                                       field_names=fields)
        records.extend(response.get("items") or [])
        if not response.get("has_more"):
            return records
        page_token = response.get("page_token", "")
        if not page_token:
            raise FeishuError("Feishu records pagination omitted page token")


def _fields(row: dict, labels: dict[str, str]) -> dict:
    result = row["analysis"] or {}
    actions = result.get("action_items") or []
    deadlines = sorted(item["deadline_iso"] for item in actions if item.get("deadline_iso"))
    original = extract_content(row["raw_message"]).text
    if len(original) > ORIGINAL_TEXT_LIMIT:
        original = original[:ORIGINAL_TEXT_LIMIT] + "\n\n[飞书仅展示前 20,000 字；完整原文保留在本机邮件档案]"
    full_chinese = result.get("translation_zh") or ""
    image_info = result.get("_source_images") or {}
    remote = int(image_info.get("remote_not_loaded", 0))
    local = int(image_info.get("selected_local", 0))
    selected_remote = int(image_info.get("selected_remote", 0))
    image_note = (f"已分析随信图片 {local} 张、远程候选图片 {selected_remote} 张"
                  if row["analysis"] else "待 AI 分析图片")
    if row["analysis"] and remote:
        image_note += f"；远程图片 {remote} 张未加载，可能有未覆盖的信息"
    fields = {
        "中文标题": result.get("title_zh") or row["subject"] or "（无主题）",
        "收件时间": row["date"] or "",
        "发件人": row["sender"],
        "中文摘要": result.get("summary_zh") or "待 AI 分析",
        "完整中文内容": full_chinese,
        "需要处理": bool(result.get("action_required", False)),
        "待办": "\n".join(item["action"] for item in actions),
        "截止日期": deadlines[0] if deadlines else "",
        "图片说明": image_note,
        "处理状态": row["status"],
        "原文标题": row["subject"],
        "本地邮件ID": str(row["id"]),
        "来源邮箱": labels.get(row["account_id"], "Other"),
        "邮箱文件夹": row["mailbox"],
        "AI理解状态": "已分析" if row["analysis"] else "待分析",
        "原文内容": original,
    }
    stamp = parsedate_utc(row["date"]) if row["date"] else None
    if stamp is not None:
        fields["收件日期"] = int(stamp.timestamp() * 1000)
    return fields


def publish_pending(client: FeishuClient, archive: Archive, *, state_path: Path = STATE_PATH, limit: int = 20) -> dict:
    state = _load_state(state_path)
    if not state.get("table_id"):
        raise FeishuError("Mail Center is not provisioned")
    rows = archive.pending_feishu(limit=limit, schema_version=SCHEMA_VERSION)
    if not rows:
        return {"published": 0, "updated": 0, "reconciled": 0}
    # Reconcile after a remote success/local crash before creating anything.
    existing = {str(record.get("fields", {}).get("本地邮件ID")): record.get("record_id")
                for record in _all_records(client, state, ("本地邮件ID",))}
    labels = account_labels()
    published = 0
    updated = 0
    reconciled = 0
    creates: list[tuple[dict, dict]] = []
    updates: list[tuple[dict, dict, str]] = []
    for row in rows:
        record_id = row["record_id"] or existing.get(str(row["id"]))
        if record_id:
            analysis_current = (row["analysis_id"] == row["synced_analysis_id"] and
                                (not row["analysis_processed_at"] or
                                 row["analysis_processed_at"] <= (row["published_at"] or "")))
            if row["schema_version"] == 2 and analysis_current:
                if row["analysis"]:
                    archive.mark_published(row["id"], record_id, analysis_id=row["analysis_id"],
                                           schema_version=SCHEMA_VERSION)
                    continue
                fields = {"图片说明": "待 AI 分析图片"}
                updates.append((row, fields, record_id))
                continue
            fields = _fields(row, labels)
            # Never overwrite a handling state the human may have just edited.
            fields.pop("处理状态", None)
            updates.append((row, fields, record_id))
            if not row["record_id"]:
                reconciled += 1
        else:
            creates.append((row, _fields(row, labels)))
    for offset in range(0, len(updates), 10):
        batch = updates[offset:offset + 10]
        client.batch_update_records(state["app_token"], state["table_id"], [
            {"record_id": record_id, "fields": fields}
            for _, fields, record_id in batch
        ])
        for row, _, record_id in batch:
            archive.mark_published(row["id"], record_id, analysis_id=row["analysis_id"],
                                   schema_version=SCHEMA_VERSION)
            updated += 1
    for offset in range(0, len(creates), 10):
        batch = creates[offset:offset + 10]
        response = client.batch_create_records(state["app_token"], state["table_id"],
                                               [fields for _, fields in batch])
        records = response.get("records") or []
        if len(records) != len(batch):
            raise FeishuError("Feishu batch create did not return every record")
        for (row, _), record in zip(batch, records):
            record_id = record.get("record_id")
            if not record_id:
                raise FeishuError("Feishu batch create omitted a record ID")
            archive.mark_published(row["id"], record_id, analysis_id=row["analysis_id"],
                                   schema_version=SCHEMA_VERSION)
            published += 1
    return {"published": published, "updated": updated, "reconciled": reconciled}


def pull_statuses(client: FeishuClient, archive: Archive, *, state_path: Path = STATE_PATH) -> dict:
    state = _load_state(state_path)
    if not state.get("table_id"):
        raise FeishuError("Mail Center is not provisioned")
    mapping = archive.record_mapping()
    updated = 0
    for record in _all_records(client, state):
        message_id = mapping.get(record.get("record_id"))
        if message_id is None:
            continue
        status = record.get("fields", {}).get("处理状态")
        if status in STATUSES:
            archive.set_status(message_id, status)
            updated += 1
    return {"statuses_read": updated}
