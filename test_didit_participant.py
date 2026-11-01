"""Participant identity flow, account binding, and callback trust boundaries.

Uses the isolated SQLite fixture from test_didit_webhook_db, never the app DB.
Provider HTTP calls are mocked; no paid or real identity session is created.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
import time
from unittest.mock import MagicMock, patch
import unittest
import uuid

os.environ["DIDIT_WORKFLOW_ID"] = ""
os.environ["DIDIT_API_KEY"] = ""
import test_didit_webhook_db as fixtures
from api import main
from app.models import BackgroundJob, DiditSession, User, Verification
from app.verification import didit_client, didit_processor
from app.verification.didit import canonical_json_v2, verify_signature_v2
from app.jobs.service import DIDIT_SESSION_RECONCILE, enqueue_didit_session_reconcile_job

WORKFLOW = "11111111-2222-4333-8444-555555555555"


class ParticipantDiditTests(fixtures.DiditWebhookDatabaseTests):
    def config(self):
        return patch.dict(os.environ, {"DIDIT_API_KEY": "unit-test-api-key", "DIDIT_WORKFLOW_ID": WORKFLOW})

    def provider(self, **extra):
        return {"session_id": str(uuid.uuid4()), "workflow_id": WORKFLOW,
                "vendor_data": str(self.user_id), "status": "Not Started",
                "url": "https://verify.didit.me/session/unit-test", **extra}

    def login(self):
        self.client.cookies.set("user_id", str(self.user_id))

    def start(self, provider=None, body=None):
        self.login()
        with self.config(), patch.object(main, "create_didit_session", return_value=provider or self.provider()) as create:
            response = self.client.post("/api/verification/didit/session", json=body or {})
        return response, create

    def test_start_binds_current_user_and_ignores_client_results(self):
        provider = self.provider()
        response, create = self.start(provider, {"user_id": 999, "session_id": "stolen", "provider_status": "Approved", "workflow_id": "attacker"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["url"], provider["url"])
        self.assertIn("no-store", response.headers["cache-control"])
        create.assert_called_once_with(user_id=self.user_id, callback=f"{main.BASE_URL.rstrip('/')}/verify/didit/return")
        with main.SessionLocal() as db:
            row = db.query(DiditSession).one()
            self.assertEqual(row.user_id, self.user_id)
            self.assertEqual(row.status, "pending")
            job = db.query(BackgroundJob).filter(BackgroundJob.job_type == DIDIT_SESSION_RECONCILE).one()
            self.assertEqual(job.payload_json["session_id"], provider["session_id"])

    def test_status_and_start_require_login(self):
        self.assertEqual(self.client.get("/api/verification/status").status_code, 401)
        with self.config():
            self.assertEqual(self.client.post("/api/verification/didit/session", json={}).status_code, 401)

    def test_unconfigured_and_provider_failure_do_not_create_local_session(self):
        self.login()
        with patch.dict(os.environ, {"DIDIT_API_KEY": ""}):
            self.assertEqual(self.client.post("/api/verification/didit/session", json={}).status_code, 503)
        with self.config(), patch.object(main, "create_didit_session", side_effect=ValueError("private provider response")):
            response = self.client.post("/api/verification/didit/session", json={})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private provider", response.text)
        with main.SessionLocal() as db:
            self.assertEqual(db.query(DiditSession).count(), 0)

    def test_foreign_origin_and_form_posts_are_rejected(self):
        self.login()
        with self.config(), patch.object(main, "create_didit_session") as create:
            self.assertEqual(self.client.post("/api/verification/didit/session", json={}, headers={"Origin": "https://other.example"}).status_code, 403)
            self.assertEqual(self.client.post("/api/verification/didit/session", data={"session_id": "x"}).status_code, 415)
            create.assert_not_called()

    def test_email_verified_is_not_identity_verified(self):
        with main.SessionLocal() as db:
            user = db.get(User, self.user_id)
            user.verification_status = "verified"
            user.verified_email = user.email
            db.add(Verification(user_id=user.id, method="email_otp", status="verified", attribute="email", evidence_ref="email-fixture"))
            db.commit()
        self.login()
        payload = self.client.get("/api/verification/status").json()
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(payload["identity_status"], "unverified")

    def test_callback_status_query_cannot_approve_pending_session(self):
        provider = self.provider()
        self.start(provider)
        response = self.client.get("/verify/didit/return", params={"verificationSessionId": provider["session_id"], "status": "Approved"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(self.client.get("/api/verification/status").json()["identity_status"], "pending")
        with main.SessionLocal() as db:
            self.assertEqual(db.query(Verification).count(), 0)
            self.assertLess(db.query(BackgroundJob).one().run_after, datetime.utcnow() + timedelta(seconds=5))

    def test_callback_and_poll_reject_other_users_session(self):
        provider = self.provider()
        self.start(provider)
        with main.SessionLocal() as db:
            other = User(email="other@example.com", password="x")
            db.add(other)
            db.commit()
            other_id = other.id
        self.client.cookies.set("user_id", str(other_id))
        self.assertEqual(self.client.get("/verify/didit/return", params={"verificationSessionId": provider["session_id"]}).status_code, 404)
        self.assertEqual(self.client.get("/api/verification/status", params={"session_id": provider["session_id"]}).status_code, 404)

    def test_signed_result_updates_participant_and_start_does_not_reset_it(self):
        provider = self.provider()
        self.start(provider)
        body = {"event_id": str(uuid.uuid4()), "webhook_type": "status.updated",
                "session_id": provider["session_id"], "vendor_data": str(self.user_id), "workflow_id": WORKFLOW, "status": "Approved"}
        signed, headers = fixtures._signed_request(body, self.secret)
        with self.config():
            response = self.client.post("/webhook/didit", json=signed, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = self.client.get("/api/verification/status").json()
        self.assertEqual(payload["identity_status"], "verified")
        self.assertEqual(payload["didit"]["status"], "verified")
        response, create = self.start(provider)
        self.assertEqual(response.json()["status"], "verified")
        create.assert_not_called()

    def test_repeated_start_reuses_registered_session(self):
        provider = self.provider()
        self.start(provider)
        self.start(provider)
        with main.SessionLocal() as db:
            self.assertEqual(db.query(DiditSession).count(), 1)
            self.assertEqual(db.query(BackgroundJob).count(), 1)

    def test_reconcile_rejects_wrong_account_and_workflow(self):
        provider = self.provider()
        self.start(provider)
        with main.SessionLocal() as db:
            for override in ({"vendor_data": "999"}, {"workflow_id": "other"}):
                with patch.object(didit_processor, "fetch_session_decision", return_value={**provider, "status": "Approved", **override}):
                    with self.assertRaises(RuntimeError):
                        didit_processor.reconcile_didit_session(db, session_id=provider["session_id"], user_id=self.user_id)
            self.assertEqual(db.query(Verification).count(), 0)

    def test_reconcile_recovers_missed_webhook(self):
        provider = self.provider()
        self.start(provider)
        with main.SessionLocal() as db, patch.object(didit_processor, "fetch_session_decision", return_value={**provider, "status": "Approved"}):
            didit_processor.reconcile_didit_session(db, session_id=provider["session_id"], user_id=self.user_id)
            db.commit()
        self.assertEqual(self.client.get("/api/verification/status").json()["identity_status"], "verified")

    def test_late_event_cannot_undo_new_result_and_decline_revokes_evidence(self):
        provider = self.provider()
        self.start(provider)
        now = int(time.time())
        base = {"webhook_type": "status.updated", "session_id": provider["session_id"], "vendor_data": str(self.user_id)}
        with main.SessionLocal() as db:
            for offset, status in ((-10, "Approved"), (-20, "In Progress")):
                didit_processor.process_didit_webhook_body(db, {**base, "event_id": str(uuid.uuid4()), "status": status, "created_at": now + offset})
                db.commit()
            self.assertEqual(db.query(DiditSession).one().status, "verified")
            didit_processor.process_didit_webhook_body(db, {**base, "event_id": str(uuid.uuid4()), "status": "Declined", "created_at": now})
            db.commit()
            self.assertEqual(db.query(Verification).one().status, "rejected")
            self.assertIsNone(db.get(User, self.user_id).verified_tier)
        self.assertEqual(self.client.get("/api/verification/status").json()["identity_status"], "rejected")

    def test_completed_reconcile_does_not_block_future_checks(self):
        provider = self.provider()
        self.start(provider)
        with main.SessionLocal() as db:
            old = db.query(BackgroundJob).one()
            old.status = "done"
            db.commit()
            future = datetime.utcnow() + timedelta(minutes=6)
            with patch("app.jobs.service.datetime") as clock:
                clock.utcnow.return_value = future
                job = enqueue_didit_session_reconcile_job(db, session_id=provider["session_id"], user_id=self.user_id)
            self.assertNotEqual(job.id, old.id)
            self.assertEqual(job.status, "pending")

    def test_expired_evidence_is_not_identity_verified(self):
        body = self._approved_body()
        with main.SessionLocal() as db:
            didit_processor.process_didit_webhook_body(db, body)
            db.flush()
            db.query(Verification).one().expires_at = datetime.utcnow() - timedelta(days=1)
            db.commit()
        self.login()
        payload = self.client.get("/api/verification/status").json()
        self.assertEqual(payload["identity_status"], "expired")
        self.assertEqual(payload["didit"]["status"], "expired")

    def test_webhook_retry_does_not_store_document_data(self):
        body = {**self._approved_body(), "decision": {"document_number": "private-document", "face_image": "private-photo"}}
        signed, headers = fixtures._signed_request(body, self.secret)
        with patch.object(main, "process_didit_webhook_body", side_effect=RuntimeError("temporary failure")):
            self.assertEqual(self.client.post("/webhook/didit", json=signed, headers=headers).status_code, 200)
        with main.SessionLocal() as db:
            payload = json.dumps(db.query(BackgroundJob).one().payload_json)
            self.assertNotIn("private-document", payload)
            self.assertNotIn("private-photo", payload)

    def test_researcher_cannot_read_unrelated_participant(self):
        self.login()
        with main.SessionLocal() as db:
            other = User(email="unrelated@example.com", password="x")
            db.add(other)
            db.commit()
            other_id = other.id
        self.assertEqual(self.client.get(f"/api/participants/{other_id}/verification").status_code, 403)

    def test_profile_dashboard_and_verify_offer_identity_card(self):
        self.login()
        self.client.cookies.set("participant_app", "1")
        for path in ("/profile", "/dashboard", "/dashboard/mobile", "/verify"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("data-didit-verification", response.text, path)
            self.assertIn("/static/didit_verification.js", response.text, path)


class DiditClientTests(unittest.TestCase):
    def test_provider_request_uses_server_binding_and_validates_response(self):
        response = MagicMock()
        response.json.return_value = {"session_id": "test-session", "url": "https://verify.didit.me/session/test", "vendor_data": "42", "workflow_id": WORKFLOW}
        with patch.dict(os.environ, {"DIDIT_API_KEY": "secret", "DIDIT_WEBHOOK_SECRET": "secret", "DIDIT_WORKFLOW_ID": WORKFLOW}), patch.object(didit_client.httpx, "Client") as client:
            client.return_value.__enter__.return_value.post.return_value = response
            payload = didit_client.create_session(user_id=42, callback="https://example.com/verify/didit/return")
            self.assertEqual(payload["session_id"], "test-session")
            sent = client.return_value.__enter__.return_value.post.call_args.kwargs
            self.assertEqual(sent["json"], {"workflow_id": WORKFLOW, "vendor_data": "42", "callback": "https://example.com/verify/didit/return"})
            response.json.return_value["vendor_data"] = "999"
            with self.assertRaises(ValueError):
                didit_client.create_session(user_id=42, callback="https://example.com")

    def test_hosted_url_rejects_untrusted_destinations(self):
        for url in ("javascript:alert(1)", "http://verify.didit.me/session/test", "https://verify.didit.me.attacker.example/", "https://attacker@verify.didit.me/", "https://verify.didit.me:444/"):
            with self.assertRaises(ValueError, msg=url):
                didit_client.verification_url(url)

    def test_signature_timestamp_cannot_be_refreshed_by_changing_header(self):
        now = int(time.time())
        body = {"timestamp": now - 600, "status": "Approved"}
        signature = hmac.new(b"secret", canonical_json_v2(body).encode(), hashlib.sha256).hexdigest()
        self.assertFalse(verify_signature_v2(body, signature, str(now), "secret", now=now))


if __name__ == "__main__":
    unittest.main()
