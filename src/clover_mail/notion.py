"""Optional, one-way Notion mirror of the private local mail archive."""

from __future__ import annotations

import json
import os
import fcntl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date

from .apple_mail import account_labels, parsedate_utc
from .archive import Archive
from .content import extract_content

API_ROOT = "https://api.notion.com/v1"
API_VERSION = "2026-03-11"
SCHEMA_VERSION = 1
TEXT_CHUNK = 1800
BLOCK_BATCH = 60


class NotionError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class NotionConfig:
    token: str
    data_source_id: str

    @classmethod
    def from_environment(cls) -> "NotionConfig":
        token = os.environ.get("CLOVER_MAIL_NOTION_TOKEN", "").strip()
        data_source_id = os.environ.get("CLOVER_MAIL_NOTION_DATA_SOURCE_ID", "").strip()
        if not token or not data_source_id:
            raise NotionError("CLOVER_MAIL_NOTION_TOKEN and CLOVER_MAIL_NOTION_DATA_SOURCE_ID are required")
        return cls(token, data_source_id)


def notion_configured() -> bool:
    return bool(os.environ.get("CLOVER_MAIL_NOTION_TOKEN") or
                os.environ.get("CLOVER_MAIL_NOTION_DATA_SOURCE_ID"))


class NotionClient:
    def __init__(self, config: NotionConfig) -> None:
        self.config = config

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(API_ROOT + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.config.token,
            "Notion-Version": API_VERSION,
            "Content-Type": "application/json; charset=utf-8",
        })
        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    result = json.load(response)
                if not isinstance(result, dict):
                    raise NotionError("Notion returned an invalid response")
                return result
            except urllib.error.HTTPError as exc:
                safe_overload_retry = method == "GET" or path.endswith("/query")
                if (exc.code == 429 or (exc.code == 529 and safe_overload_retry)) and attempt < 4:
                    header = exc.headers.get("Retry-After", "")
                    delay = max(1, int(header)) if header.isdigit() else min(2 ** attempt, 30)
                    time.sleep(delay)
                    continue
                try:
                    failure = json.load(exc)
                    code = failure.get("code", "unknown")
                except (ValueError, UnicodeDecodeError):
                    code = "unknown"
                raise NotionError(f"Notion HTTP {exc.code}, code {code} at {path}",
                                  status=exc.code) from None
            except urllib.error.URLError:
                raise NotionError("Notion API could not be reached") from None
            except (ValueError, UnicodeDecodeError):
                raise NotionError("Notion returned an invalid response") from None
        raise NotionError("Notion request retry limit reached")

    def get_data_source(self) -> dict:
        return self._call("GET", f"/data_sources/{self.config.data_source_id}")

    def find_page(self, mail_id: int) -> str | None:
        response = self._call("POST", f"/data_sources/{self.config.data_source_id}/query", {
            "filter": {"property": "本地邮件ID", "rich_text": {"equals": str(mail_id)}},
            "page_size": 2,
        })
        pages = response.get("results") or []
        if len(pages) > 1:
            raise NotionError(f"duplicate Notion pages for local mail ID {mail_id}")
        return pages[0].get("id") if pages else None

    def create_page(self, properties: dict, blocks: list[dict]) -> str:
        response = self._call("POST", "/pages", {
            "parent": {"type": "data_source_id", "data_source_id": self.config.data_source_id},
            "properties": properties,
            "children": blocks[:BLOCK_BATCH],
        })
        page_id = response.get("id")
        if not page_id:
            raise NotionError("Notion created a page without returning its ID")
        self.append_blocks(page_id, blocks[BLOCK_BATCH:])
        return page_id

    def update_page(self, page_id: str, properties: dict) -> None:
        self._call("PATCH", f"/pages/{page_id}", {"properties": properties})

    def list_child_ids(self, page_id: str) -> list[str]:
        ids: list[str] = []
        cursor = ""
        while True:
            query = "?page_size=100"
            if cursor:
                query += "&start_cursor=" + urllib.parse.quote(cursor, safe="")
            response = self._call("GET", f"/blocks/{page_id}/children{query}")
            ids.extend(block["id"] for block in response.get("results", []) if block.get("id"))
            if not response.get("has_more"):
                return ids
            cursor = response.get("next_cursor", "")
            if not cursor:
                raise NotionError("Notion block pagination omitted next_cursor")

    def append_blocks(self, page_id: str, blocks: list[dict]) -> None:
        for offset in range(0, len(blocks), BLOCK_BATCH):
            self._call("PATCH", f"/blocks/{page_id}/children", {
                "children": blocks[offset:offset + BLOCK_BATCH]
            })

    def replace_blocks(self, page_id: str, blocks: list[dict]) -> None:
        # Pages in this mirror are generated content. A failed replacement is
        # rebuilt on the next pass because the local checkpoint is written last.
        for block_id in self.list_child_ids(page_id):
            self._call("DELETE", f"/blocks/{block_id}")
        self.append_blocks(page_id, blocks)


def _rich_text(value: str, *, limit: int = 1900) -> list[dict]:
    return [{"type": "text", "text": {"content": value[:limit]}}] if value else []


def _plain_block(kind: str, value: str) -> dict:
    return {"object": "block", "type": kind,
            kind: {"rich_text": _rich_text(value, limit=TEXT_CHUNK)}}


def _text_blocks(value: str) -> list[dict]:
    clean = value.replace("\x00", "")
    if not clean:
        clean = "（无可读内容）"
    return [_plain_block("paragraph", clean[pos:pos + TEXT_CHUNK])
            for pos in range(0, len(clean), TEXT_CHUNK)]


def _deadline(analysis: dict) -> str | None:
    dates = sorted(str(item.get("deadline_iso")) for item in
                   analysis.get("action_items", []) if item.get("deadline_iso"))
    for value in dates:
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            continue
    return None


def _page_content(row: dict, labels: dict[str, str]) -> tuple[dict, list[dict]]:
    analysis = row["analysis"] or {}
    original = extract_content(row["raw_message"]).text
    translation = str(analysis.get("translation_zh") or "")
    actions = analysis.get("action_items") or []
    summary = str(analysis.get("summary_zh") or "")
    title = str(analysis.get("title_zh") or row["subject"] or "（无主题）")
    stamp = parsedate_utc(row["date"]) if row["date"] else None
    deadline = _deadline(analysis)
    properties = {
        "中文标题": {"title": _rich_text(title)},
        "本地邮件ID": {"rich_text": _rich_text(str(row["id"]))},
        "来源邮箱": {"select": {"name": labels.get(row["account_id"], "Other")}},
        "发件人": {"rich_text": _rich_text(row["sender"])},
        "中文摘要": {"rich_text": _rich_text(summary or "待 AI 分析")},
        "待办": {"rich_text": _rich_text("；".join(str(item.get("action", "")) for item in actions))},
        "处理状态": {"select": {"name": row["status"]}},
        "AI理解状态": {"select": {"name": "已分析" if row["analysis"] else "待分析"}},
        "原文标题": {"rich_text": _rich_text(row["subject"])},
        "邮箱文件夹": {"rich_text": _rich_text(row["mailbox"])},
        "收件日期": {"date": {"start": stamp.isoformat()} if stamp else None},
        "截止日期": {"date": {"start": deadline} if deadline else None},
    }
    blocks = [_plain_block("heading_2", "邮件信息")]
    blocks += _text_blocks(f"本地邮件ID：{row['id']}\n来源邮箱：{labels.get(row['account_id'], 'Other')}"
                           f"\n原文标题：{row['subject']}\n发件人：{row['sender']}\n收件时间：{row['date'] or ''}")
    blocks.append(_plain_block("heading_2", "AI 理解"))
    blocks += _text_blocks("中文摘要：" + (summary or "待 AI 分析"))
    if actions:
        blocks += _text_blocks("待办：\n" + "\n".join(str(item.get("action", "")) for item in actions))
    if deadline:
        blocks += _text_blocks("截止日期：" + deadline)
    important = analysis.get("important_facts") or []
    if important:
        blocks += _text_blocks("值得回查的信息：\n" + "\n".join(map(str, important)))
    image = analysis.get("_source_images") or {}
    if image:
        blocks += _text_blocks("图片分析覆盖：" + json.dumps(image, ensure_ascii=False))
    blocks.append(_plain_block("heading_2", "完整中文翻译"))
    blocks += _text_blocks(translation or "待 AI 分析；原文已完整同步。")
    blocks.append(_plain_block("heading_2", "邮件原文"))
    blocks += _text_blocks(original)
    return properties, blocks


def publish_pending(client: NotionClient, archive: Archive, *, limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("--limit must be between 1 and 100")
    lock_path = archive.directory / "notion.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"busy": True}
        rows = archive.pending_notion(limit=limit, schema_version=SCHEMA_VERSION)
        labels = account_labels()
        created = updated = reconciled = 0
        for row in rows:
            properties, blocks = _page_content(row, labels)
            page_id = row["page_id"] or client.find_page(row["id"])
            if page_id:
                client.update_page(page_id, properties)
                client.replace_blocks(page_id, blocks)
                updated += 1
                if not row["page_id"]:
                    reconciled += 1
            else:
                page_id = client.create_page(properties, blocks)
                created += 1
            archive.mark_notion_published(row["id"], page_id,
                                          analysis_id=row["analysis_id"],
                                          schema_version=SCHEMA_VERSION)
        return {"created": created, "updated": updated, "reconciled": reconciled}
    finally:
        os.close(lock_fd)
