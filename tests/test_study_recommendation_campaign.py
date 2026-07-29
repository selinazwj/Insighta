import hashlib
import os
import unittest
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:////private/tmp/insighta_campaign_pytest.db"
os.environ["EMAIL_PROVIDER"] = "resend"
os.environ["RESEND_API_KEY"] = "test-key"
os.environ["EMAIL_FROM"] = "Insighta <test@example.com>"
os.environ["ADMIN_KEY"] = "test-admin-key"

from fastapi.testclient import TestClient

from api import main
from app.models import Survey, User, UserEvent


class StudyRecommendationCampaignTest(unittest.TestCase):
    def test_campaign_dry_run_send_and_unsubscribe(self):
        main.Base.metadata.drop_all(bind=main.engine)
        main.Base.metadata.create_all(bind=main.engine)
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


if __name__ == "__main__":
    unittest.main()
