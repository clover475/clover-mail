from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.mimo import MiMoConfig, MiMoError, analyze_email, parse_json_object, _translation_chunks
from clover_mail.content import LocalImage


ANALYSIS = {
    "useful": True,
    "title_zh": "课程注册提醒",
    "summary_zh": "学校提醒收件人完成课程注册。",
    "translation_zh": "Please register for courses by October 6.",
    "action_required": True,
    "action_items": [{"action": "完成课程注册", "deadline_iso": "2026-10-06", "evidence": "by October 6"}],
    "important_facts": [],
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class MiMoTests(unittest.TestCase):
    def test_actionable_mail_requires_a_concrete_action(self):
        config = MiMoConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        invalid = dict(ANALYSIS, action_items=[])
        with patch("clover_mail.mimo._request", return_value=(json.dumps(invalid), {})):
            with self.assertRaisesRegex(MiMoError, "concrete action"):
                analyze_email(sender="", subject="", date="", body="Register", config=config)

    def test_long_translation_is_split_without_losing_text(self):
        body = "First paragraph.\n" * 400
        chunks = _translation_chunks(body)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks).replace("\n", ""), body.replace("\n", ""))

    def test_missing_english_translation_uses_full_body_fallback(self):
        config = MiMoConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        first = dict(ANALYSIS, translation_zh="")
        body = "Please complete your registration before the deadline. " * 8
        with patch("clover_mail.mimo._request", side_effect=[(json.dumps(first), {"total_tokens": 10}), ("请在截止日期前完成注册。", {"total_tokens": 12})]) as call:
            result = analyze_email(sender="", subject="", date="", body=body, config=config)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["translation_zh"], "请在截止日期前完成注册。")
        self.assertEqual(result["_translation_usages"][0]["total_tokens"], 12)

    def test_accepts_fenced_or_embedded_json_object(self):
        self.assertEqual(parse_json_object('```json\n{"ok":true}\n```', context="query plan"), {"ok": True})
        self.assertEqual(parse_json_object('Result: {"ok":true}', context="query plan"), {"ok": True})
        with self.assertRaisesRegex(MiMoError, "not valid JSON"):
            parse_json_object('Result without JSON', context="query plan")

    def test_anthropic_endpoint_uses_configured_token_plan_protocol(self):
        config = MiMoConfig(
            api_key="test-secret",
            base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
            model="mimo-v2.6-flash",
            max_tokens=256,
        )
        payload = {"content": [{"type": "text", "text": json.dumps(ANALYSIS, ensure_ascii=False)}]}
        with patch("clover_mail.mimo.urllib.request.urlopen", return_value=FakeResponse(payload)) as request_call:
            result = analyze_email(sender="teacher@example.edu", subject="Registration", date="", body="By October 6", config=config)
        request = request_call.call_args.args[0]
        self.assertEqual(request.full_url, "https://token-plan-cn.xiaomimimo.com/anthropic/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "test-secret")
        self.assertEqual(result["action_items"][0]["deadline_iso"], "2026-10-06")
        body = json.loads(request.data)
        self.assertIn("By October 6", body["messages"][0]["content"])
        self.assertEqual(body["model"], "mimo-v2.6-flash")

    def test_openai_endpoint_is_supported(self):
        config = MiMoConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        payload = {"choices": [{"message": {"content": json.dumps(ANALYSIS, ensure_ascii=False)}}]}
        with patch("clover_mail.mimo.urllib.request.urlopen", return_value=FakeResponse(payload)) as request_call:
            result = analyze_email(sender="", subject="", date="", body="Test", config=config)
        request = request_call.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.xiaomimimo.com/v1/chat/completions")
        self.assertEqual(request.get_header("Api-key"), "test-secret")
        self.assertEqual(result["title_zh"], "课程注册提醒")

    def test_http_errors_do_not_expose_email_content(self):
        config = MiMoConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        failure = __import__("urllib.error").error.HTTPError("https://example.invalid", 401, "unauthorized", {}, io.BytesIO(b"private prompt"))
        with patch("clover_mail.mimo.urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(MiMoError) as raised:
                analyze_email(sender="", subject="secret subject", date="", body="private email body", config=config)
        failure.close()
        self.assertEqual(str(raised.exception), "MiMo request failed with HTTP 401")
        self.assertNotIn("private email body", str(raised.exception))

    def test_local_image_is_sent_as_data_url(self):
        config = MiMoConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        payload = {"choices": [{"message": {"content": json.dumps(ANALYSIS, ensure_ascii=False)}}]}
        image = LocalImage("image/png", "poster.png", b"synthetic-image", 800, 1100)
        with patch("clover_mail.mimo.urllib.request.urlopen", return_value=FakeResponse(payload)) as request_call:
            analyze_email(sender="", subject="", date="", body="Text", images=(image,), config=config)
        sent = json.loads(request_call.call_args.args[0].data)
        content = sent["messages"][1]["content"]
        self.assertEqual(content[0]["text"].splitlines()[-1], "Text")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(sent["max_completion_tokens"], config.max_tokens)

    def test_truncated_translation_is_not_accepted(self):
        config = MiMoConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        payload = {"choices": [{"finish_reason": "length", "message": {"content": json.dumps(ANALYSIS)}}]}
        with patch("clover_mail.mimo.urllib.request.urlopen", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(MiMoError, "truncated"):
                analyze_email(sender="", subject="", date="", body="Long content", config=config)


if __name__ == "__main__":
    unittest.main()
