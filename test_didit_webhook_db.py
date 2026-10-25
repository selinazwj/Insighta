"""E2E tests: signed Didit webhook writes session + verification evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import unittest
import uuid
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:////tmp/insighta_didit_webhook_db_test.db"
os.environ["VERIFICATION_EVIDENCE_SECRET"] = "didit-db-test-secret"
os.environ["DIDIT_WEBHOOK_SECRET"] = "didit-webhook-test-secret"
os.environ["ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient

from api import main
from app.models import DiditSession, DiditWebhookEvent, User, Verification
from app.verification.assurance import TIER_TRUSTED, evidence_reference
from app.verification.didit import canonical_json_v2
from app.verification.didit_processor import process_didit_webhook_body


def _signed_request(body: dict, secret: str) -> tuple[dict, dict]:
    timestamp = int(time.time())
    signed_body = {**body, "timestamp": timestamp}
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical_json_v2(signed_body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": str(timestamp),
        "X-Signature-V2": signature,
    }
    return signed_body, headers


class DiditWebhookDatabaseTests(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(bind=main.engine)
        main.Base.metadata.create_all(bind=main.engine)
        db = main.SessionLocal()
        try:
            user = User(email="didit-participant@example.com", password="x")
            db.add(user)
            db.commit()
            self.user_id = user.id
        finally:
            db.close()
        self.client = TestClient(main.app)
        self.secret = os.environ["DIDIT_WEBHOOK_SECRET"]

    def _approved_body(self) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "webhook_type": "status.updated",
            "session_id": str(uuid.uuid4()),
            "status": "Approved",
            "vendor_data": str(self.user_id),
            "workflow_id": str(uuid.uuid4()),
        }

    def test_signed_webhook_writes_session_and_verification(self):
        body = self._approved_body()
        signed_body, headers = _signed_request(body, self.secret)
        response = self.client.post(
            "/webhook/didit",
            data=json.dumps(signed_body),
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("status"), "ok")

        db = main.SessionLocal()
        try:
            session = db.query(DiditSession).filter(
                DiditSession.session_id == body["session_id"]
            ).one()
            self.assertEqual(session.user_id, self.user_id)
            self.assertEqual(session.status, "verified")
            self.assertEqual(session.provider_status, "Approved")

            ref = evidence_reference("didit", body["session_id"])
            evidence = db.query(Verification).filter(
                Verification.user_id == self.user_id,
                Verification.method == "didit",
                Verification.evidence_ref == ref,
            ).one()
            self.assertEqual(evidence.status, "verified")

            user = db.query(User).filter(User.id == self.user_id).one()
            self.assertEqual(user.verification_status, "verified")
            self.assertEqual(user.verified_tier, TIER_TRUSTED)

            event = db.query(DiditWebhookEvent).filter(
                DiditWebhookEvent.event_id == body["event_id"]
            ).one()
            self.assertEqual(event.session_id, body["session_id"])
        finally:
            db.close()

    def test_duplicate_event_id_is_idempotent(self):
        body = self._approved_body()
        db = main.SessionLocal()
        try:
            process_didit_webhook_body(db, body)
            db.commit()
        finally:
            db.close()

        signed_body, headers = _signed_request(body, self.secret)
        response = self.client.post(
            "/webhook/didit",
            data=json.dumps(signed_body),
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("duplicate"))

        db = main.SessionLocal()
        try:
            count = db.query(DiditWebhookEvent).filter(
                DiditWebhookEvent.event_id == body["event_id"]
            ).count()
            self.assertEqual(count, 1)
        finally:
            db.close()

    def test_transient_failure_queues_background_job(self):
        body = self._approved_body()
        signed_body, headers = _signed_request(body, self.secret)

        with patch(
            "api.main.process_didit_webhook_body",
            side_effect=RuntimeError("db locked"),
        ):
            response = self.client.post(
                "/webhook/didit",
                data=json.dumps(signed_body),
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("queued"))

        db = main.SessionLocal()
        try:
            from app.models import BackgroundJob
            from app.jobs.service import DIDIT_WEBHOOK_PROCESS

            job = db.query(BackgroundJob).filter(
                BackgroundJob.job_type == DIDIT_WEBHOOK_PROCESS,
                BackgroundJob.dedupe_key == f"{DIDIT_WEBHOOK_PROCESS}:{body['event_id']}",
            ).one()
            self.assertEqual(job.payload_json.get("event_id"), body["event_id"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
