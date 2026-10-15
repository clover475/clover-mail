from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.ai import (AIConfig, AIError, MAX_PROVIDER_RESPONSE_BYTES, _NoRedirect,
                            analyze_email, parse_json_object, _request, _translation_chunks)
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

    def read(self, limit=-1):
        data = json.dumps(self.payload).encode()
        return data if limit < 0 else data[:limit]


class AIProviderTests(unittest.TestCase):
    def test_actionable_mail_requires_a_concrete_action(self):
        config = AIConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        invalid = dict(ANALYSIS, action_items=[])
        with patch("clover_mail.ai._request", return_value=(json.dumps(invalid), {})):
            with self.assertRaisesRegex(AIError, "concrete action"):
                analyze_email(sender="", subject="", date="", body="Register", config=config)

    def test_long_translation_is_split_without_losing_text(self):
        body = "First paragraph.\n" * 400
        chunks = _translation_chunks(body)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks).replace("\n", ""), body.replace("\n", ""))

    def test_missing_english_translation_uses_full_body_fallback(self):
        config = AIConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        first = dict(ANALYSIS, translation_zh="")
        body = "Please complete your registration before the deadline. " * 8
        with patch("clover_mail.ai._request", side_effect=[(json.dumps(first), {"total_tokens": 10}), ("请在截止日期前完成注册。", {"total_tokens": 12})]) as call:
            result = analyze_email(sender="", subject="", date="", body=body, config=config)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["translation_zh"], "请在截止日期前完成注册。")
        self.assertEqual(result["_translation_usages"][0]["total_tokens"], 12)

    def test_accepts_fenced_or_embedded_json_object(self):
        self.assertEqual(parse_json_object('```json\n{"ok":true}\n```', context="query plan"), {"ok": True})
        self.assertEqual(parse_json_object('Result: {"ok":true}', context="query plan"), {"ok": True})
        with self.assertRaisesRegex(AIError, "not valid JSON"):
            parse_json_object('Result without JSON', context="query plan")

    def test_anthropic_endpoint_uses_configured_token_plan_protocol(self):
        config = AIConfig(
            api_key="test-secret",
            base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
            model="mimo-v2.6-flash",
            max_tokens=256,
        )
        payload = {"content": [{"type": "text", "text": json.dumps(ANALYSIS, ensure_ascii=False)}]}
        with patch("clover_mail.ai._open_request", return_value=FakeResponse(payload)) as request_call:
            result = analyze_email(sender="teacher@example.edu", subject="Registration", date="", body="By October 6", config=config)
        request = request_call.call_args.args[0]
        self.assertEqual(request.full_url, "https://token-plan-cn.xiaomimimo.com/anthropic/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "test-secret")
        self.assertEqual(result["action_items"][0]["deadline_iso"], "2026-10-06")
        body = json.loads(request.data)
        self.assertIn("By October 6", body["messages"][0]["content"])
        self.assertEqual(body["model"], "mimo-v2.6-flash")

    def test_openai_compatible_endpoint_uses_bearer_auth(self):
        config = AIConfig(api_key="test-secret", base_url="https://api.openai.com/v1",
                          model="gpt-example", provider="openai-compatible")
        payload = {"choices": [{"message": {"content": json.dumps(ANALYSIS, ensure_ascii=False)}}]}
        with patch("clover_mail.ai._open_request", return_value=FakeResponse(payload)) as request_call:
            result = analyze_email(sender="", subject="", date="", body="Test", config=config)
        request = request_call.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(result["title_zh"], "课程注册提醒")

    def test_native_anthropic_provider_uses_messages_api(self):
        config = AIConfig(api_key="test-secret", base_url="https://api.anthropic.com",
                          model="claude-example", provider="anthropic")
        payload = {"content": [{"type": "text", "text": json.dumps(ANALYSIS, ensure_ascii=False)}]}
        with patch("clover_mail.ai._open_request", return_value=FakeResponse(payload)) as request_call:
            analyze_email(sender="", subject="", date="", body="Test", config=config)
        request = request_call.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_header("X-api-key"), "test-secret")

    def test_http_errors_do_not_expose_email_content(self):
        config = AIConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        failure = __import__("urllib.error").error.HTTPError("https://example.invalid", 401, "unauthorized", {}, io.BytesIO(b"private prompt"))
        with patch("clover_mail.ai._open_request", side_effect=failure):
            with self.assertRaises(AIError) as raised:
                analyze_email(sender="", subject="secret subject", date="", body="private email body", config=config)
        failure.close()
        self.assertEqual(str(raised.exception), "AI provider request failed with HTTP 401")
        self.assertNotIn("private email body", str(raised.exception))

    def test_provider_redirects_are_rejected(self):
        redirect = _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://other.example")
        self.assertIsNone(redirect)

    def test_provider_response_is_size_limited(self):
        class LargeResponse(FakeResponse):
            def read(self, limit=-1):
                return b"x" * limit

        config = AIConfig(api_key="synthetic", base_url="https://api.example.com/v1",
                          model="test-model", provider="openai-compatible")
        with patch("clover_mail.ai._open_request", return_value=LargeResponse({})):
            with self.assertRaisesRegex(AIError, "size limit"):
                _request(config, "synthetic message")

    def test_local_image_is_sent_as_data_url(self):
        config = AIConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        payload = {"choices": [{"message": {"content": json.dumps(ANALYSIS, ensure_ascii=False)}}]}
        image = LocalImage("image/png", "poster.png", b"synthetic-image", 800, 1100)
        with patch("clover_mail.ai._open_request", return_value=FakeResponse(payload)) as request_call:
            analyze_email(sender="", subject="", date="", body="Text", images=(image,), config=config)
        sent = json.loads(request_call.call_args.args[0].data)
        content = sent["messages"][1]["content"]
        self.assertEqual(json.loads(content[0]["text"])["body"], "Text")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(sent["max_completion_tokens"], config.max_tokens)

    def test_truncated_translation_is_not_accepted(self):
        config = AIConfig(api_key="test-secret", base_url="https://api.xiaomimimo.com/v1", model="mimo-v2.6-flash")
        payload = {"choices": [{"finish_reason": "length", "message": {"content": json.dumps(ANALYSIS)}}]}
        with patch("clover_mail.ai._open_request", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(AIError, "truncated"):
                analyze_email(sender="", subject="", date="", body="Long content", config=config)

    def test_provider_config_requires_explicit_model_for_non_default_vendor(self):
        values = {"CLOVER_MAIL_AI_PROVIDER": "anthropic", "CLOVER_MAIL_AI_API_KEY": "synthetic"}
        with patch.dict("os.environ", values, clear=True):
            with self.assertRaisesRegex(AIError, "CLOVER_MAIL_AI_MODEL is required"):
                AIConfig.from_environment()

    def test_provider_configuration_selects_generic_model_and_separate_cache_key(self):
        values = {
            "CLOVER_MAIL_AI_PROVIDER": "openai-compatible",
            "CLOVER_MAIL_AI_API_KEY": "synthetic",
            "CLOVER_MAIL_AI_MODEL": "gpt-example",
        }
        with patch.dict("os.environ", values, clear=True):
            config = AIConfig.from_environment()
        self.assertEqual(config.base_url, "https://api.openai.com/v1")
        self.assertEqual(config.storage_model, "openai-compatible:gpt-example")

    def test_endpoint_rejects_http_credentials_and_query_strings(self):
        endpoints = (
            "http://api.example.com/v1",
            "https://user:pass@example.com/v1",
            "https://api.example.com/v1?token=leak",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(AIError, "HTTPS URL"):
                AIConfig("synthetic", endpoint, "model")


if __name__ == "__main__":
    unittest.main()
