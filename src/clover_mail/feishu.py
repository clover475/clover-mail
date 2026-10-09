"""Small Feishu OpenAPI adapter for a private Mail Center Base."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API_ROOT = "https://open.feishu.cn/open-apis"


class FeishuError(RuntimeError):
    pass


def _env_file(path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name in {"FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID", "FEISHU_OPEN_ID"}:
            result[name] = value.strip().strip('"\'')
    return result


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    chat_id: str = ""
    open_id: str = ""

    @classmethod
    def from_environment(cls) -> "FeishuConfig":
        values = _env_file(os.environ["FEISHU_ENV_FILE"]) if os.environ.get("FEISHU_ENV_FILE") else {}
        values.update({k: v for k, v in os.environ.items() if k in {
            "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID", "FEISHU_OPEN_ID"
        }})
        if not values.get("FEISHU_APP_ID") or not values.get("FEISHU_APP_SECRET"):
            raise FeishuError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")
        return cls(values["FEISHU_APP_ID"], values["FEISHU_APP_SECRET"],
                   values.get("FEISHU_CHAT_ID", ""), values.get("FEISHU_OPEN_ID", ""))


class FeishuClient:
    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self._token = ""

    def _call(self, method: str, path: str, payload: dict | None = None, *, authenticated: bool = True) -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.tenant_token()
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = urllib.request.Request(API_ROOT + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                failure = json.load(exc)
                code = failure.get("code", "unknown")
                scopes = re.search(r"scopes is required: (\[[^\]]+\])", str(failure.get("msg", "")))
                detail = f"; required scopes {scopes.group(1)}" if scopes else ""
            except (ValueError, UnicodeDecodeError, AttributeError):
                code, detail = "unknown", ""
            raise FeishuError(f"Feishu HTTP {exc.code}, code {code} at {path}{detail}") from None
        except urllib.error.URLError:
            raise FeishuError("Feishu API could not be reached") from None
        except (ValueError, UnicodeDecodeError):
            raise FeishuError("Feishu returned an invalid response") from None
        if not isinstance(result, dict) or result.get("code") != 0:
            code = result.get("code", "unknown") if isinstance(result, dict) else "unknown"
            raise FeishuError(f"Feishu API code {code} at {path}")
        return result.get("data", result)

    def tenant_token(self) -> str:
        if not self._token:
            result = self._call("POST", "/auth/v3/tenant_access_token/internal", {
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            }, authenticated=False)
            self._token = result.get("tenant_access_token", "")
            if not self._token:
                raise FeishuError("Feishu did not return a tenant token")
        return self._token

    def create_base(self, name: str) -> dict:
        return self._call("POST", "/bitable/v1/apps", {"name": name})

    def create_table(self, app_token: str, name: str, fields: list[dict]) -> dict:
        token = urllib.parse.quote(app_token, safe="")
        return self._call("POST", f"/bitable/v1/apps/{token}/tables", {
            "table": {"name": name, "default_view_name": "全部邮件", "fields": fields}
        })

    def list_fields(self, app_token: str, table_id: str) -> dict:
        return self._call("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100")

    def create_field(self, app_token: str, table_id: str, definition: dict) -> dict:
        return self._call("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", definition)

    def list_views(self, app_token: str, table_id: str) -> dict:
        return self._call("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views?page_size=100")

    def create_view(self, app_token: str, table_id: str, name: str) -> dict:
        return self._call("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
                          {"view_name": name, "view_type": "grid"})

    def update_view(self, app_token: str, table_id: str, view_id: str, property: dict) -> dict:
        return self._call("PATCH", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view_id}",
                          {"property": property})

    def create_record(self, app_token: str, table_id: str, fields: dict) -> dict:
        app = urllib.parse.quote(app_token, safe="")
        table = urllib.parse.quote(table_id, safe="")
        return self._call("POST", f"/bitable/v1/apps/{app}/tables/{table}/records", {"fields": fields})

    def batch_create_records(self, app_token: str, table_id: str, records: list[dict]) -> dict:
        app = urllib.parse.quote(app_token, safe="")
        table = urllib.parse.quote(table_id, safe="")
        return self._call("POST", f"/bitable/v1/apps/{app}/tables/{table}/records/batch_create",
                          {"records": [{"fields": fields} for fields in records]})

    def batch_update_records(self, app_token: str, table_id: str, records: list[dict]) -> dict:
        app = urllib.parse.quote(app_token, safe="")
        table = urllib.parse.quote(table_id, safe="")
        return self._call("POST", f"/bitable/v1/apps/{app}/tables/{table}/records/batch_update",
                          {"records": records})

    def list_records(self, app_token: str, table_id: str, page_token: str = "",
                     field_names: tuple[str, ...] = ()) -> dict:
        app = urllib.parse.quote(app_token, safe="")
        table = urllib.parse.quote(table_id, safe="")
        query = "?page_size=100"
        if field_names:
            query += "&field_names=" + urllib.parse.quote(json.dumps(field_names, ensure_ascii=False), safe="")
        if page_token:
            query += "&page_token=" + urllib.parse.quote(page_token, safe="")
        return self._call("GET", f"/bitable/v1/apps/{app}/tables/{table}/records{query}")

    def update_record(self, app_token: str, table_id: str, record_id: str, fields: dict) -> dict:
        app = urllib.parse.quote(app_token, safe="")
        table = urllib.parse.quote(table_id, safe="")
        record = urllib.parse.quote(record_id, safe="")
        return self._call("PUT", f"/bitable/v1/apps/{app}/tables/{table}/records/{record}", {"fields": fields})

    def send_text(self, text: str, *, open_id: str | None = None, chat_id: str | None = None) -> dict:
        if open_id or self.config.open_id:
            recipient, recipient_type = open_id or self.config.open_id, "open_id"
        elif chat_id or self.config.chat_id:
            recipient, recipient_type = chat_id or self.config.chat_id, "chat_id"
        else:
            raise FeishuError("FEISHU_OPEN_ID or FEISHU_CHAT_ID is required to send a message")
        return self._call("POST", f"/im/v1/messages?receive_id_type={recipient_type}", {
            "receive_id": recipient,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        })
