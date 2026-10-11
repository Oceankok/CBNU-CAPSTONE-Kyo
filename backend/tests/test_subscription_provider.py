"""No real CLI, OAuth login, model, or network is used by these tests."""

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import httpx

from backend.equipment import chatgpt_auth, provider, subscription_provider as adapters


RESULT = {"candidates": [{"label": "프레스", "equipment_type": "press", "confidence": None, "reason": "작업대와 압착 장치"}]}


class SubscriptionProviderTests(unittest.TestCase):
    def test_provider_selection_is_explicit(self):
        with patch.dict(os.environ, {"PPE_EQUIPMENT_PROVIDER": ""}):
            self.assertIsNone(provider.get_provider())
        for mode in ("codex_chatgpt", "chatgpt_oauth"):
            with patch.dict(os.environ, {"PPE_EQUIPMENT_PROVIDER": mode}):
                self.assertEqual(provider.get_provider().selected, mode)

    def test_codex_requires_chatgpt_login_and_never_uses_api_keys(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret", "CODEX_API_KEY": "test-secret"}), \
                patch.object(adapters, "codex_executable", return_value="codex.exe"), \
                patch.object(adapters, "codex_signed_in", return_value=False) as check, \
                patch.object(adapters.subprocess, "run") as run:
            with self.assertRaises(provider.ProviderError) as error:
                adapters.SubscriptionProvider("codex_chatgpt").analyze(b"redacted", timeout_seconds=30)
            self.assertEqual(error.exception.code, "provider_auth_required")
            self.assertNotIn("OPENAI_API_KEY", check.call_args.args[1])
            self.assertNotIn("CODEX_API_KEY", check.call_args.args[1])
            run.assert_not_called()

    def test_codex_receives_redacted_image_and_cleans_temporary_files(self):
        with TemporaryDirectory() as directory:
            def run(command, **kwargs):
                root = Path(kwargs["cwd"])
                self.assertEqual((root / "image.jpg").read_bytes(), b"redacted")
                self.assertIn('forced_login_method="chatgpt"', command)
                self.assertIn("read-only", command)
                self.assertIn("--ephemeral", command)
                self.assertIn("shell_tool", command)
                self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
                (root / "result.json").write_text(json.dumps(RESULT), encoding="utf-8")
                return Mock(returncode=0)
            with patch.object(adapters, "WORK_ROOT", Path(directory)), \
                    patch.object(adapters, "codex_executable", return_value="codex.exe"), \
                    patch.object(adapters, "codex_signed_in", return_value=True), \
                    patch.object(adapters.subprocess, "run", side_effect=run):
                result = adapters.SubscriptionProvider("codex_chatgpt").analyze(b"redacted", timeout_seconds=30)
            self.assertEqual(result.candidates[0].equipment_type, "press")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def oauth_response(self, events, status=200):
        response = Mock(status_code=status)
        response.iter_lines.return_value = ["data: " + json.dumps(event) for event in events]
        client = Mock()
        client.stream.return_value.__enter__ = Mock(return_value=response)
        client.stream.return_value.__exit__ = Mock(return_value=False)
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=False)
        return context, client

    def test_oauth_uses_public_streaming_endpoint_and_only_completed_result(self):
        context, client = self.oauth_response([
            {"type": "response.output_text.delta", "delta": "ignored partial text"},
            {"type": "response.completed", "response": {"output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(RESULT)}]}]}}
        ])
        with patch.dict(os.environ, {"PPE_EQUIPMENT_MODEL": "account-model"}), \
                patch.object(chatgpt_auth, "access_token", return_value="test-oauth-token"), \
                patch.object(adapters.httpx, "Client", return_value=context):
            result = adapters.SubscriptionProvider("chatgpt_oauth").analyze(b"redacted", timeout_seconds=30)
        self.assertEqual(result.candidates[0].label, "프레스")
        self.assertEqual(client.stream.call_args.args[1], "https://api.openai.com/v1/responses")
        payload = client.stream.call_args.kwargs["json"]
        self.assertTrue(payload["stream"])
        self.assertFalse(payload["store"])
        self.assertNotIn("max_output_tokens", payload)
        self.assertIn("cmVkYWN0ZWQ=", payload["input"][0]["content"][1]["image_url"])

    def test_oauth_failure_after_partial_output_is_not_success(self):
        for events, expected in [
            ([{"type": "response.output_text.delta", "delta": json.dumps(RESULT)}], "provider_response_invalid"),
            ([{"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}], "provider_usage_limit"),
            ([{"type": "response.incomplete"}], "provider_response_invalid")
        ]:
            context, _ = self.oauth_response(events)
            with patch.dict(os.environ, {"PPE_EQUIPMENT_MODEL": "account-model"}), \
                    patch.object(chatgpt_auth, "access_token", return_value="test-oauth-token"), \
                    patch.object(adapters.httpx, "Client", return_value=context):
                with self.assertRaises(provider.ProviderError) as error:
                    adapters.SubscriptionProvider("chatgpt_oauth").analyze(b"redacted", timeout_seconds=30)
            self.assertEqual(error.exception.code, expected)

    def test_oauth_completed_without_snapshot_uses_buffered_deltas(self):
        encoded = json.dumps(RESULT)
        context, _ = self.oauth_response([
            {"type": "response.output_text.delta", "delta": encoded[:25]},
            {"type": "response.output_text.delta", "delta": encoded[25:]},
            {"type": "response.completed", "response": {"output": []}}
        ])
        with patch.dict(os.environ, {"PPE_EQUIPMENT_MODEL": "account-model"}), \
                patch.object(chatgpt_auth, "access_token", return_value="test-oauth-token"), \
                patch.object(adapters.httpx, "Client", return_value=context):
            result = adapters.SubscriptionProvider("chatgpt_oauth").analyze(b"redacted", timeout_seconds=30)
        self.assertEqual(result.candidates[0].equipment_type, "press")

    def test_timeout_and_invalid_json_are_sanitized(self):
        with patch.object(adapters.SubscriptionProvider, "_codex", side_effect=subprocess.TimeoutExpired("secret", 30)):
            with self.assertRaises(provider.ProviderError) as error:
                adapters.SubscriptionProvider("codex_chatgpt").analyze(b"redacted", timeout_seconds=30)
            self.assertEqual(error.exception.code, "provider_timeout")
        with self.assertRaises(provider.ProviderError) as error:
            adapters.parse_result("private invalid response")
        self.assertEqual(error.exception.code, "provider_response_invalid")

    def test_rotation_is_saved_and_scope_loss_blocks_inference(self):
        record = {"client_id": "test-client", "subject": "test-sub", "access_token": "old",
                  "refresh_token": "refresh-old", "expires_at": 0, "scopes": ["chatgpt.tokens.use.direct"]}
        with TemporaryDirectory() as directory, patch.object(chatgpt_auth, "ROOT", Path(directory)), \
                patch.object(chatgpt_auth, "load_credentials", return_value=record), \
                patch.object(chatgpt_auth, "save_credentials") as save, \
                patch.object(chatgpt_auth, "token_request", return_value={"access_token": "new", "refresh_token": "refresh-new", "expires_in": 3600}) as request:
            self.assertEqual(chatgpt_auth.access_token(), "new")
            self.assertEqual(save.call_args.args[0]["refresh_token"], "refresh-new")
            self.assertNotIn("scope", request.call_args.args[0])
            record["scopes"] = ["openid"]
            with self.assertRaises(provider.ProviderError) as error:
                chatgpt_auth.access_token()
            self.assertEqual(error.exception.code, "provider_auth_required")

    def test_invalid_nonce_and_identity_are_rejected(self):
        response = Mock()
        response.json.return_value = {"keys": [{"kid": "key"}]}
        with patch.object(chatgpt_auth.httpx, "Client") as client, \
                patch.object(chatgpt_auth.jwt, "get_unverified_header", return_value={"alg": "RS256", "kid": "key"}), \
                patch.object(chatgpt_auth.jwt.PyJWK, "from_dict", return_value=Mock(key="key")), \
                patch.object(chatgpt_auth.jwt, "decode", return_value={"sub": "test-sub", "nonce": "wrong"}):
            client.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaises(provider.ProviderError):
                chatgpt_auth.validate_identity("fake-id-token", "test-client", "expected")
