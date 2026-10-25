"""Unit tests for Didit signature verification and webhook status mapping."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch

from app.verification.didit import (
    DIDIT_METHOD,
    canonical_json_v2,
    is_didit_test_webhook,
    map_provider_status,
    parse_vendor_user_id,
    verify_didit_webhook,
    verify_signature_v2,
)


def _sign_v2(body: dict, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_json_v2(body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class DiditWebhookHelpersTests(unittest.TestCase):
    def test_map_provider_status(self):
        self.assertEqual(map_provider_status("Approved"), "verified")
        self.assertEqual(map_provider_status("Declined"), "rejected")
        self.assertEqual(map_provider_status("In Progress"), "pending")
        self.assertEqual(map_provider_status("In Review"), "pending")
        self.assertEqual(map_provider_status("something-new"), "pending")

    def test_parse_vendor_user_id(self):
        self.assertEqual(parse_vendor_user_id("42"), 42)
        self.assertEqual(parse_vendor_user_id("user_42"), 42)
        self.assertEqual(parse_vendor_user_id(None, {"user_id": 7}), 7)
        self.assertIsNone(parse_vendor_user_id("not-a-user"))

    def test_signature_v2_accepts_valid_payload(self):
        secret = "test-shared-secret"
        now = int(time.time())
        body = {
            "event_id": "9c0c8b8a-1111-4222-9333-444444444444",
            "webhook_type": "status.updated",
            "timestamp": now,
            "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "status": "Approved",
            "vendor_data": "42",
            "decision": {"status": "Approved", "score": 1.0},
        }
        signature = _sign_v2(body, secret)
        self.assertTrue(
            verify_signature_v2(body, signature, str(now), secret, now=float(now))
        )
        self.assertTrue(
            verify_didit_webhook(
                body,
                {
                    "X-Timestamp": str(now),
                    "X-Signature-V2": signature,
                },
                secrets=[secret],
                now=float(now),
            )
        )

    def test_signature_v2_rejects_tampered_body_and_stale_timestamp(self):
        secret = "test-shared-secret"
        now = int(time.time())
        body = {
            "event_id": "evt-1",
            "webhook_type": "status.updated",
            "timestamp": now,
            "session_id": "sess-1",
            "status": "Approved",
            "vendor_data": "1",
        }
        signature = _sign_v2(body, secret)
        tampered = dict(body)
        tampered["status"] = "Declined"
        self.assertFalse(
            verify_signature_v2(tampered, signature, str(now), secret, now=float(now))
        )
        stale = now - 301
        stale_body = dict(body)
        stale_body["timestamp"] = stale
        stale_sig = _sign_v2(stale_body, secret)
        self.assertFalse(
            verify_signature_v2(
                stale_body, stale_sig, str(stale), secret, now=float(now)
            )
        )

    def test_previous_signature_header_is_accepted(self):
        secret = "current-secret"
        previous = "previous-secret"
        now = int(time.time())
        body = {
            "event_id": "evt-2",
            "webhook_type": "status.updated",
            "timestamp": now,
            "session_id": "sess-2",
            "status": "In Progress",
            "vendor_data": "9",
        }
        previous_sig = _sign_v2(body, previous)
        self.assertTrue(
            verify_didit_webhook(
                body,
                {
                    "x-timestamp": str(now),
                    "x-signature-v2-previous": previous_sig,
                },
                secrets=[secret, previous],
                now=float(now),
            )
        )

    def test_method_constant(self):
        self.assertEqual(DIDIT_METHOD, "didit")

    def test_detects_didit_console_test_header(self):
        self.assertTrue(is_didit_test_webhook({"X-Didit-Test-Webhook": "true"}))
        self.assertFalse(is_didit_test_webhook({"X-Didit-Test-Webhook": "false"}))
        self.assertFalse(is_didit_test_webhook({}))


class DiditWebhookRouteTests(unittest.TestCase):
    """Light route-level checks that do not require the integration DB fixture."""

    def test_webhook_rejects_when_secret_missing(self):
        from fastapi.testclient import TestClient
        from api import main as main_module

        with patch.object(main_module, "didit_webhook_secrets", return_value=[]):
            client = TestClient(main_module.app)
            response = client.post("/webhook/didit", json={"webhook_type": "status.updated"})
        self.assertEqual(response.status_code, 503)

    def test_webhook_rejects_bad_signature(self):
        from fastapi.testclient import TestClient
        from api import main as main_module

        with patch.object(
            main_module, "didit_webhook_secrets", return_value=["unit-test-secret"]
        ):
            client = TestClient(main_module.app)
            now = str(int(time.time()))
            response = client.post(
                "/webhook/didit",
                data=json.dumps({
                    "event_id": "evt-bad",
                    "webhook_type": "status.updated",
                    "timestamp": int(now),
                    "session_id": "sess-bad",
                    "status": "Approved",
                    "vendor_data": "1",
                }),
                headers={
                    "Content-Type": "application/json",
                    "X-Timestamp": now,
                    "X-Signature-V2": "00" * 32,
                },
            )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
