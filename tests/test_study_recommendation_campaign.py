import hashlib
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:////private/tmp/insighta_campaign_pytest.db"
os.environ["EMAIL_PROVIDER"] = "resend"
os.environ["RESEND_API_KEY"] = "test-key"
os.environ["EMAIL_FROM"] = "Insighta <test@example.com>"
os.environ["ADMIN_KEY"] = "test-admin-key"

from fastapi.testclient import TestClient

from api import main
from app.jobs.service import FAILED, PENDING, RUNNING, recover_stale_running_jobs
from app.models import BackgroundJob, Survey, User, UserEvent


class StudyRecommendationCampaignTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(bind=main.engine)
        main.Base.metadata.create_all(bind=main.engine)

    def test_campaign_dry_run_send_and_unsubscribe(self):
        db = main.SessionLocal()
        publisher = User(email="publisher@example.com", password="x", username="Publisher")
        recipient = User(email="participant@example.com", password="x", first_name="Ada")
        opted_out = User(email="optout@example.com", password="x")
        db.add_all([publisher, recipient, opted_out])
        db.flush()
        surveys = [
            Survey(
                publisher_id=publisher.id,
                title="Study One",
                description="First paid study.",
                form_url="https://example.com/one",
                category="research",
                estimated_time=30,
                share_slug="study-one",
                reward_amount=25,
                admin_display_reward_amount=30,
                target_responses=10,
                status="published",
            ),
            Survey(
                publisher_id=publisher.id,
                title="Study Two",
                description="Second paid study.",
                form_url="https://example.com/two",
                category="research",
                estimated_time=45,
                share_slug="study-two",
                reward_amount=40,
                admin_display_reward_amount=50,
                target_responses=10,
                status="published",
            ),
        ]
        token = "one-time-token"
        db.add_all(surveys)
        db.add(UserEvent(
            user_id=opted_out.id,
            event_name=main.STUDY_RECOMMENDATION_UNSUBSCRIBED_EVENT,
            target_type="email_preference",
            target_id="study_recommendations",
        ))
        db.add(UserEvent(
            event_name=main.STUDY_RECOMMENDATION_CAMPAIGN_EVENT,
            target_type="email_campaign",
            target_id="campaign-test",
            metadata_json={
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "study_slugs": ["study-one", "study-two"],
                "status": "ready",
            },
        ))
        db.commit()
        db.close()

        captured = []

        def fake_batch(messages, *, idempotency_key):
            captured.extend(messages)
            return True, [f"resend-{index}" for index in range(len(messages))], None

        client = TestClient(main.app)
        with patch.object(main, "send_email_batch", side_effect=fake_batch):
            dry_run = client.post(
                "/internal/email-campaigns/campaign-test/send",
                json={"token": token, "dry_run": True},
            )
            self.assertEqual(dry_run.status_code, 200)
            self.assertEqual(dry_run.json()["recipient_count"], 2)
            self.assertEqual(dry_run.json()["opted_out_count"], 1)

            sent = client.post(
                "/internal/email-campaigns/campaign-test/send",
                json={"token": token, "dry_run": False, "confirm": "SEND"},
            )
            self.assertEqual(sent.status_code, 200)
            self.assertEqual(sent.json()["sent_count"], 2)
            self.assertEqual(len(captured), 2)
            self.assertTrue(all("List-Unsubscribe" in message["headers"] for message in captured))

            repeated = client.post(
                "/internal/email-campaigns/campaign-test/send",
                json={"token": token, "dry_run": False, "confirm": "SEND"},
            )
            self.assertEqual(repeated.status_code, 409)

    def test_admin_background_jobs_summary_hides_large_payload_fields(self):
        db = main.SessionLocal()
        db.add_all([
            BackgroundJob(
                job_type="admin_bulk_email",
                status="pending",
                payload_json={
                    "user_id": 123,
                    "email": "participant@example.com",
                    "subject": "Internal subject",
                    "message": "Long email body should not be exposed in the monitor.",
                },
            ),
            BackgroundJob(
                job_type="trust_data_cleanup",
                status="failed",
                payload_json={"cleanup_window": "20260807"},
                attempts=2,
                max_attempts=2,
                last_error="cleanup failed",
            ),
        ])
        db.commit()
        db.close()

        client = TestClient(main.app)
        unauthorized = client.get("/admin/background-jobs?admin_key=wrong")
        self.assertEqual(unauthorized.status_code, 403)

        response = client.get("/admin/background-jobs?admin_key=test-admin-key")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["counts"]["pending"], 1)
        self.assertEqual(data["counts"]["failed"], 1)
        pending_job = next(job for job in data["jobs"] if job["status"] == "pending")
        self.assertEqual(pending_job["payload"]["user_id"], 123)
        self.assertNotIn("message", pending_job["payload"])
        self.assertNotIn("subject", pending_job["payload"])

    def test_admin_can_manually_run_due_background_jobs(self):
        client = TestClient(main.app)
        unauthorized = client.post(
            "/admin/background-jobs/run-due",
            json={"admin_key": "wrong", "limit": 25},
        )
        self.assertEqual(unauthorized.status_code, 403)

        with patch.object(main, "run_due_background_jobs", return_value={
            "claimed": 2,
            "processed": 2,
            "succeeded": 1,
            "failed": 1,
            "skipped": 0,
            "enqueued": 0,
            "stale_recovered": 0,
            "stale_failed": 0,
        }) as run_mock:
            response = client.post(
                "/admin/background-jobs/run-due",
                json={"admin_key": "test-admin-key", "limit": 999},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["claimed"], 2)
        run_mock.assert_called_once_with(limit=100)

    def test_final_background_job_failure_sends_safe_alert(self):
        db = main.SessionLocal()
        db.add(BackgroundJob(
            job_type="unknown_job_type",
            status="pending",
            payload_json={
                "user_id": 456,
                "email": "participant@example.com",
                "message": "This private email body should not be exposed.",
            },
            attempts=0,
            max_attempts=1,
        ))
        db.commit()
        db.close()

        captured = []

        def fake_send_email(to, subject, body, text_body=None):
            captured.append({
                "to": to,
                "subject": subject,
                "body": body,
                "text_body": text_body,
            })
            return True, None

        with patch.object(main, "send_email", side_effect=fake_send_email):
            result = main.run_due_background_jobs(limit=5)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(captured), 1)
        self.assertIn("unknown_job_type", captured[0]["subject"])
        self.assertIn("user_id", captured[0]["text_body"])
        self.assertNotIn("private email body", captured[0]["body"])
        self.assertNotIn("private email body", captured[0]["text_body"])

    def test_retryable_background_job_failure_does_not_alert(self):
        db = main.SessionLocal()
        db.add(BackgroundJob(
            job_type="unknown_job_type",
            status="pending",
            payload_json={"user_id": 789},
            attempts=0,
            max_attempts=2,
        ))
        db.commit()
        db.close()

        with patch.object(main, "send_email") as send_email_mock:
            result = main.run_due_background_jobs(limit=5)

        self.assertEqual(result["failed"], 1)
        send_email_mock.assert_not_called()

    def test_stale_running_job_recovers_to_pending(self):
        locked_at = datetime.utcnow() - timedelta(minutes=30)
        db = main.SessionLocal()
        job = BackgroundJob(
            job_type="admin_bulk_email",
            status=RUNNING,
            payload_json={"user_id": 321},
            attempts=1,
            max_attempts=3,
            locked_at=locked_at,
        )
        db.add(job)
        db.commit()
        job_id = job.id

        result = recover_stale_running_jobs(db, now=datetime.utcnow())
        recovered = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        db.close()

        self.assertEqual(result["stale_recovered"], 1)
        self.assertEqual(result["stale_failed"], 0)
        self.assertEqual(recovered.status, PENDING)
        self.assertIsNone(recovered.locked_at)
        self.assertIn("Recovered stale running job", recovered.last_error)

    def test_stale_running_job_at_max_attempts_fails_and_calls_back(self):
        locked_at = datetime.utcnow() - timedelta(minutes=30)
        callbacks = []
        db = main.SessionLocal()
        job = BackgroundJob(
            job_type="admin_bulk_email",
            status=RUNNING,
            payload_json={"user_id": 654},
            attempts=3,
            max_attempts=3,
            locked_at=locked_at,
        )
        db.add(job)
        db.commit()
        job_id = job.id

        result = recover_stale_running_jobs(
            db,
            now=datetime.utcnow(),
            on_failure=lambda session, failed_job, error, final: callbacks.append(
                (failed_job.id, error, final)
            ),
        )
        failed = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        db.close()

        self.assertEqual(result["stale_recovered"], 0)
        self.assertEqual(result["stale_failed"], 1)
        self.assertEqual(failed.status, FAILED)
        self.assertEqual(callbacks[0][0], job_id)
        self.assertTrue(callbacks[0][2])


if __name__ == "__main__":
    unittest.main()
