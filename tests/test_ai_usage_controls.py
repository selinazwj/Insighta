import os
import unittest
from datetime import datetime
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:////tmp/insighta_ai_usage_controls_test.db"
os.environ["ANTHROPIC_API_KEY"] = "test-key"
os.environ["AI_DAILY_USER_OPERATIONS"] = "10"
os.environ["AI_DAILY_PLATFORM_UNITS"] = "500"
os.environ["AI_REQUEST_COOLDOWN_MINUTES"] = "10"

from fastapi.testclient import TestClient

from api import main
from app.ai_usage import AI_USAGE_EVENT
from app.models import Answer, BackgroundJob, Question, Response, ResponseQualityCheck, Survey, User, UserEvent
from app.quality_engine import QualityScoreResult


class AiUsageControlsTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(bind=main.engine)
        main.Base.metadata.create_all(bind=main.engine)
        db = main.SessionLocal()
        publisher = User(email="publisher-ai@example.com", password="x", username="Publisher")
        other = User(email="other-ai@example.com", password="x", username="Other")
        participant = User(email="participant-ai@example.com", password="x", username="Participant")
        db.add_all([publisher, other, participant])
        db.flush()
        survey = Survey(
            publisher_id=publisher.id,
            title="AI controls study",
            description="Test study",
            form_url="__builtin__",
            category="research",
            estimated_time=10,
            reward_amount=5,
            target_responses=20,
            status="published",
        )
        db.add(survey)
        db.flush()
        self.publisher_id = publisher.id
        self.other_id = other.id
        self.participant_id = participant.id
        self.survey_id = survey.id
        db.commit()
        db.close()
        self.client = TestClient(main.app)

    def _queue_prediction(self, **overrides):
        payload = {"mode": "summary", "scope": "quick", "force": False}
        payload.update(overrides)
        return self.client.post(
            f"/api/surveys/{self.survey_id}/prediction/jobs",
            json=payload,
            cookies={"user_id": str(self.publisher_id)},
        )

    def test_prediction_job_is_deduped_private_and_audited(self):
        first = self._queue_prediction()
        self.assertEqual(first.status_code, 202)
        first_payload = first.json()
        self.assertEqual(first_payload["units"], 20)
        self.assertFalse(first_payload["reused"])

        repeated = self._queue_prediction()
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(repeated.json()["job_id"], first_payload["job_id"])
        self.assertTrue(repeated.json()["reused"])

        forbidden = self.client.get(
            f"/api/ai-jobs/{first_payload['job_id']}",
            cookies={"user_id": str(self.other_id)},
        )
        self.assertEqual(forbidden.status_code, 404)

        fake_result = {
            "survey_id": self.survey_id,
            "candidate_count": 7,
            "completion_probability": 0.72,
            "segment_label": "Students",
            "risk_reasons": [],
            "recommended_action": "Keep recruiting",
            "llm_ok": True,
        }
        with patch("api.main.survey_prediction_summary", return_value=fake_result) as prediction:
            run = main.run_due_background_jobs(limit=25)
        self.assertEqual(run["failed"], 0)
        prediction.assert_called_once()

        completed = self.client.get(
            f"/api/ai-jobs/{first_payload['job_id']}",
            cookies={"user_id": str(self.publisher_id)},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "done")
        self.assertEqual(completed.json()["result"]["candidate_count"], 7)

        db = main.SessionLocal()
        events = db.query(UserEvent).filter(UserEvent.event_name == AI_USAGE_EVENT).all()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].metadata_json["status"], "succeeded")
        self.assertEqual(events[0].metadata_json["actual_units"], 7)
        db.close()

    def test_prediction_get_is_cache_only_and_never_calls_llm(self):
        with patch(
            "app.ai_growth.prediction.predict_participants_with_claude",
            side_effect=AssertionError("GET must not call Claude"),
        ), patch(
            "app.ai_growth.prediction.summarize_survey_with_claude",
            side_effect=AssertionError("GET must not call Claude summary"),
        ):
            response = self.client.get(
                f"/api/surveys/{self.survey_id}/prediction/summary",
                cookies={"user_id": str(self.publisher_id)},
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["llm_ok"])

    def test_shared_daily_limit_returns_429_without_plan_tiers(self):
        first = self._queue_prediction()
        self.assertEqual(first.status_code, 202)
        db = main.SessionLocal()
        event = db.query(UserEvent).filter(UserEvent.event_name == AI_USAGE_EVENT).first()
        metadata = dict(event.metadata_json or {})
        metadata["status"] = "succeeded"
        event.metadata_json = metadata
        db.commit()
        db.close()

        with patch.dict(os.environ, {"AI_DAILY_USER_OPERATIONS": "1"}, clear=False):
            blocked = self._queue_prediction(mode="respondents")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Daily AI operation limit", blocked.json()["detail"])

    def test_failed_prediction_keeps_reservation_until_retries_are_exhausted(self):
        queued = self._queue_prediction()
        self.assertEqual(queued.status_code, 202)
        job_id = queued.json()["job_id"]

        with patch("api.main.survey_prediction_summary", side_effect=RuntimeError("provider unavailable")):
            first_run = main.run_due_background_jobs(limit=25)
        self.assertEqual(first_run["failed"], 1)

        db = main.SessionLocal()
        usage = db.query(UserEvent).filter(UserEvent.event_name == AI_USAGE_EVENT).first()
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        self.assertEqual(usage.metadata_json["status"], "queued")
        self.assertEqual(job.status, "pending")
        job.run_after = datetime.utcnow()
        db.commit()
        db.close()

        with patch("api.main.survey_prediction_summary", side_effect=RuntimeError("provider unavailable")):
            final_run = main.run_due_background_jobs(limit=25)
        self.assertEqual(final_run["failed"], 1)

        db = main.SessionLocal()
        usage = db.query(UserEvent).filter(UserEvent.event_name == AI_USAGE_EVENT).first()
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        self.assertEqual(usage.metadata_json["status"], "failed")
        self.assertEqual(job.status, "failed")
        db.close()

    def test_builtin_quality_review_is_explicit_background_work(self):
        db = main.SessionLocal()
        question = Question(
            survey_id=self.survey_id,
            question_text="Why?",
            question_type="text",
            is_required=True,
            order_index=1,
        )
        response = Response(
            survey_id=self.survey_id,
            participant_id=self.participant_id,
            status="completed",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        db.add_all([question, response])
        db.flush()
        db.add(Answer(response_id=response.id, question_id=question.id, answer_value="A detailed answer"))
        check = ResponseQualityCheck(
            survey_id=self.survey_id,
            response_id=response.id,
            source_type="builtin",
            quality_score=65,
            quality_label="medium",
            review_status="pending",
        )
        db.add(check)
        db.commit()
        check_id = check.id
        db.close()

        queued = self.client.post(
            f"/api/quality-checks/{check_id}/ai-review",
            cookies={"user_id": str(self.publisher_id)},
        )
        self.assertEqual(queued.status_code, 202)

        result = QualityScoreResult(
            quality_score=88,
            quality_label="high",
            fraud_risk=False,
            rule_penalty=2,
            anomaly_score=0,
            semantic_risk=10,
            triggered_rules=[],
            reasons=["AI review completed"],
            llm_result_json={"_usage": {"input_tokens": 120, "output_tokens": 40}},
        )
        with patch("api.main.anthropic_api_key_configured", return_value=True), patch(
            "api.main.evaluate_builtin_response", return_value=result
        ):
            run = main.run_due_background_jobs(limit=25)
        self.assertEqual(run["failed"], 0)

        db = main.SessionLocal()
        updated = db.query(ResponseQualityCheck).filter(ResponseQualityCheck.id == check_id).first()
        self.assertEqual(updated.quality_score, 88)
        usage = db.query(UserEvent).filter(UserEvent.event_name == AI_USAGE_EVENT).first()
        self.assertEqual(usage.metadata_json["input_tokens"], 120)
        self.assertEqual(usage.metadata_json["output_tokens"], 40)
        db.close()


if __name__ == "__main__":
    unittest.main()
