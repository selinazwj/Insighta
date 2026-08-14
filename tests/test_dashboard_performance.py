import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/insighta_dashboard_perf_test.db")
os.environ["INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS"] = "1"
os.environ["INSIGHTA_DASHBOARD_CANDIDATE_LIMIT"] = "100"
os.environ["INSIGHTA_DASHBOARD_SCAN_LIMIT"] = "500"

from sqlalchemy import event
from fastapi.testclient import TestClient

from api import main
from app.ai_growth.models import RespondentPrediction
from app.ai_growth.llm import LLMCallResult
from app.ai_growth.prediction import MODEL_VERSION, cached_recommendations_for_user
from app.jobs.service import DASHBOARD_RECOMMENDATIONS_REFRESH
from app.models import BackgroundJob, Survey, User


class DashboardPerformanceTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(bind=main.engine)
        main.Base.metadata.create_all(bind=main.engine)
        db = main.SessionLocal()
        self.publisher = User(email="publisher-perf@example.com", password="x")
        self.participant = User(
            email="participant-perf@example.com",
            password="x",
            birth_year="1998",
            birth_month="1",
            education_level="Bachelor's degree",
            field="Computer Science & Engineering",
            status="Student",
            state="MA",
            current_province="MA",
            language="English",
            student_status="Full-time student",
            participation_format="both",
            device_type="any",
        )
        db.add_all([self.publisher, self.participant])
        db.commit()
        self.publisher_id = self.publisher.id
        self.participant_id = self.participant.id
        db.close()

    def _seed_surveys(self, count: int, **overrides):
        db = main.SessionLocal()
        now = datetime.utcnow()
        rows = []
        for index in range(count):
            values = {
                "publisher_id": self.publisher_id,
                "title": f"Performance Study {index}",
                "description": "Dashboard performance fixture.",
                "form_url": f"https://example.com/{index}",
                "category": "research",
                "estimated_time": 10,
                "share_slug": f"perf-{index}",
                "reward_amount": 5.0,
                "target_responses": 100,
                "status": "published",
                "payment_status": "paid",
                "published_at": now - timedelta(seconds=index),
            }
            values.update(overrides)
            rows.append(Survey(**values))
        db.add_all(rows)
        db.commit()
        db.close()

    def _count_queries(self, callback):
        count = 0

        def before_cursor_execute(*_args, **_kwargs):
            nonlocal count
            count += 1

        event.listen(main.engine, "before_cursor_execute", before_cursor_execute)
        try:
            result = callback()
        finally:
            event.remove(main.engine, "before_cursor_execute", before_cursor_execute)
        return result, count

    def test_ai_ranking_is_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS", None)
            self.assertTrue(main._dashboard_ai_recommendations_enabled())

    def test_candidate_query_is_bounded_for_1000_published_surveys(self):
        self._seed_surveys(1000)
        db = main.SessionLocal()
        participant = db.query(User).filter(User.id == self.participant_id).first()
        candidates, query_count = self._count_queries(
            lambda: main._dashboard_candidate_surveys(
                db,
                participant,
                exclude_existing_responses=True,
            )
        )
        db.close()

        self.assertEqual(len(candidates), 100)
        self.assertLessEqual(query_count, 2)

    def test_legacy_python_filter_scan_is_capped(self):
        self._seed_surveys(1000, target_age_range="80+")
        db = main.SessionLocal()
        participant = db.query(User).filter(User.id == self.participant_id).first()
        with patch.dict(os.environ, {
            "INSIGHTA_DASHBOARD_CANDIDATE_LIMIT": "100",
            "INSIGHTA_DASHBOARD_SCAN_LIMIT": "250",
        }, clear=False):
            candidates, query_count = self._count_queries(
                lambda: main._dashboard_candidate_surveys(
                    db,
                    participant,
                    exclude_existing_responses=True,
                )
            )
        db.close()

        self.assertEqual(candidates, [])
        # 200-row page + 50-row page; it must not walk all 1000 legacy rows.
        self.assertLessEqual(query_count, 2)

    def test_ai_cache_read_is_one_bulk_query(self):
        self._seed_surveys(100)
        db = main.SessionLocal()
        participant = db.query(User).filter(User.id == self.participant_id).first()
        surveys = db.query(Survey).order_by(Survey.id.asc()).all()
        expires_at = datetime.utcnow() + timedelta(hours=1)
        db.add_all([
            RespondentPrediction(
                survey_id=survey.id,
                participant_id=participant.id,
                model_version=MODEL_VERSION,
                probability=0.75,
                confidence="high",
                expires_at=expires_at,
            )
            for survey in surveys
        ])
        db.commit()
        # Re-load after commit so SQLAlchemy expire-on-commit is not counted.
        participant = db.query(User).filter(User.id == self.participant_id).first()
        surveys = db.query(Survey).order_by(Survey.id.asc()).all()

        recommendations, query_count = self._count_queries(
            lambda: cached_recommendations_for_user(db, surveys, participant)
        )
        db.close()

        self.assertEqual(len(recommendations), 100)
        self.assertEqual(query_count, 1)

    def test_dashboard_slow_request_log_includes_path(self):
        self._seed_surveys(1)
        client = TestClient(main.app)
        with patch("api.main.slow_request_threshold_ms", return_value=0), self.assertLogs(
            "insighta.api", level="WARNING"
        ) as captured:
            response = client.get(
                "/dashboard",
                cookies={"user_id": str(self.participant_id)},
                headers={"user-agent": "Mozilla/5.0 desktop"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any("slow_request method=GET path=/dashboard" in message for message in captured.output),
            captured.output,
        )

    def test_cached_ai_score_controls_dashboard_order(self):
        db = main.SessionLocal()
        now = datetime.utcnow()
        low_ai = Survey(
            publisher_id=self.publisher_id,
            title="Newest Low AI",
            description="Generic study",
            form_url="https://example.com/low",
            category="research",
            estimated_time=10,
            share_slug="low-ai",
            reward_amount=5.0,
            target_responses=100,
            status="published",
            payment_status="paid",
            published_at=now,
        )
        high_ai = Survey(
            publisher_id=self.publisher_id,
            title="Older High AI",
            description="Generic study",
            form_url="https://example.com/high",
            category="research",
            estimated_time=10,
            share_slug="high-ai",
            reward_amount=5.0,
            target_responses=100,
            status="published",
            payment_status="paid",
            published_at=now - timedelta(days=1),
        )
        db.add_all([low_ai, high_ai])
        db.flush()
        expires_at = now + timedelta(hours=1)
        db.add_all([
            RespondentPrediction(
                survey_id=low_ai.id,
                participant_id=self.participant_id,
                model_version=MODEL_VERSION,
                probability=0.10,
                confidence="high",
                expires_at=expires_at,
            ),
            RespondentPrediction(
                survey_id=high_ai.id,
                participant_id=self.participant_id,
                model_version=MODEL_VERSION,
                probability=0.90,
                confidence="high",
                expires_at=expires_at,
            ),
        ])
        db.commit()
        db.close()

        client = TestClient(main.app)
        with patch.dict(os.environ, {"INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS": "1"}, clear=False):
            response = client.get(
                "/dashboard",
                cookies={"user_id": str(self.participant_id)},
                headers={"user-agent": "Mozilla/5.0 desktop"},
            )

        self.assertEqual(response.status_code, 200)
        high_pos = response.text.find("Older High AI")
        low_pos = response.text.find("Newest Low AI")
        self.assertGreaterEqual(high_pos, 0)
        self.assertGreaterEqual(low_pos, 0)
        self.assertLess(high_pos, low_pos)

    def test_cold_cache_uses_local_sort_and_enqueues_ai_without_sync_llm(self):
        self._seed_surveys(10)
        client = TestClient(main.app)

        db = main.SessionLocal()
        participant = db.query(User).filter(User.id == self.participant_id).first()
        relevant = Survey(
            publisher_id=self.publisher_id,
            title="Computer Science Research",
            description="Software programming study",
            target_field="Computer Science & Engineering",
        )
        generic = Survey(
            publisher_id=self.publisher_id,
            title="General Research",
            description="General study",
        )
        self.assertGreater(
            main._recommendation_sort_score(relevant, participant, {}),
            main._recommendation_sort_score(generic, participant, {}),
        )
        db.close()

        with patch.dict(os.environ, {
            "INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS": "1",
            "INSIGHTA_DASHBOARD_CANDIDATE_LIMIT": "100",
            "INSIGHTA_DASHBOARD_SCAN_LIMIT": "500",
        }, clear=False), patch(
            "api.main.recommend_surveys_for_user",
            side_effect=AssertionError("dashboard must not call Claude synchronously"),
        ) as sync_ai:
            response = client.get(
                "/dashboard",
                cookies={"user_id": str(self.participant_id)},
                headers={"user-agent": "Mozilla/5.0 desktop"},
            )

        self.assertEqual(response.status_code, 200)
        sync_ai.assert_not_called()
        db = main.SessionLocal()
        jobs = db.query(BackgroundJob).filter(
            BackgroundJob.job_type == DASHBOARD_RECOMMENDATIONS_REFRESH,
        ).all()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].payload_json["user_id"], self.participant_id)
        self.assertEqual(len(jobs[0].payload_json["survey_ids"]), 10)
        db.close()

    def test_background_worker_fills_ai_recommendation_cache(self):
        self._seed_surveys(3)
        client = TestClient(main.app)
        response = client.get(
            "/dashboard",
            cookies={"user_id": str(self.participant_id)},
            headers={"user-agent": "Mozilla/5.0 desktop"},
        )
        self.assertEqual(response.status_code, 200)

        def fake_rank(payload):
            return LLMCallResult(data={
                "recommendations": [
                    {
                        "survey_id": item["survey_id"],
                        "completion_probability": 0.82,
                        "confidence": "high",
                        "top_reasons": ["Strong fit"],
                        "risk_reasons": [],
                        "recommended_action": "Recommend",
                        "ranking_note": "Async dashboard refresh test",
                    }
                    for item in payload.get("surveys", [])
                ]
            })

        with patch("app.ai_growth.prediction.rank_surveys_with_claude", side_effect=fake_rank):
            result = main.run_due_background_jobs(limit=25)

        self.assertGreaterEqual(result["succeeded"], 1)
        db = main.SessionLocal()
        rows = db.query(RespondentPrediction).filter(
            RespondentPrediction.participant_id == self.participant_id,
            RespondentPrediction.model_version == MODEL_VERSION,
        ).all()
        job = db.query(BackgroundJob).filter(
            BackgroundJob.job_type == DASHBOARD_RECOMMENDATIONS_REFRESH,
        ).first()
        db.close()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(abs(row.probability - 0.82) < 1e-9 for row in rows))
        self.assertIsNotNone(job)
        self.assertEqual(job.status, "done")

    def test_dashboard_query_count_does_not_scale_10_100_1000_with_ai_enabled(self):
        client = TestClient(main.app)
        query_counts = {"/dashboard": {}, "/dashboard/mobile": {}}
        for count in (10, 100, 1000):
            main.Base.metadata.drop_all(bind=main.engine)
            main.Base.metadata.create_all(bind=main.engine)
            db = main.SessionLocal()
            publisher = User(email=f"publisher-{count}@example.com", password="x")
            participant = User(
                email=f"participant-{count}@example.com",
                password="x",
                birth_year="1998",
                birth_month="1",
                field="Computer Science & Engineering",
                language="English",
                participation_format="both",
                device_type="any",
            )
            db.add_all([publisher, participant])
            db.flush()
            now = datetime.utcnow()
            db.add_all([
                Survey(
                    publisher_id=publisher.id,
                    title=f"Study {count}-{index}",
                    description="Performance fixture.",
                    form_url=f"https://example.com/{count}/{index}",
                    category="research",
                    estimated_time=10,
                    share_slug=f"route-{count}-{index}",
                    reward_amount=5.0,
                    target_responses=100,
                    status="published",
                    payment_status="paid",
                    published_at=now - timedelta(seconds=index),
                )
                for index in range(count)
            ])
            db.commit()
            participant_id = participant.id
            db.close()

            with patch.dict(os.environ, {
                "INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS": "1",
                "INSIGHTA_DASHBOARD_CANDIDATE_LIMIT": "100",
                "INSIGHTA_DASHBOARD_SCAN_LIMIT": "500",
            }, clear=False):
                for path, user_agent in (
                    ("/dashboard", "Mozilla/5.0 desktop"),
                    ("/dashboard/mobile", "Mozilla/5.0 (iPhone) Mobile/15E148"),
                ):
                    response, query_count = self._count_queries(
                        lambda path=path, user_agent=user_agent: client.get(
                            path,
                            cookies={"user_id": str(participant_id)},
                            headers={"user-agent": user_agent},
                        )
                    )
                    self.assertEqual(response.status_code, 200)
                    query_counts[path][count] = query_count

        for path in ("/dashboard", "/dashboard/mobile"):
            self.assertLessEqual(query_counts[path][10], 26)
            self.assertLessEqual(query_counts[path][100], 26)
            self.assertLessEqual(query_counts[path][1000], 26)
            self.assertLessEqual(query_counts[path][1000] - query_counts[path][10], 2)


if __name__ == "__main__":
    unittest.main()
