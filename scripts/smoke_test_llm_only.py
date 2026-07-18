"""Offline smoke tests for the LLM-only Insighta prediction integration.

This script does not call Anthropic. It monkeypatches Claude responses and checks:
- single prediction normalization/cache
- batch respondent prediction
- participant dashboard survey ranking
- preview and summary output shapes

Run from the project root:
    python scripts/smoke_test_llm_only.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC
from pathlib import Path
import os
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Importing api.main runs compatibility migrations. Force those side effects into
# a disposable database so this smoke test can never modify survey.db or a
# DATABASE_URL supplied by the developer's shell.
_APP_DB_TEMP_DIR = tempfile.TemporaryDirectory(prefix="insighta-llm-smoke-")
_APP_DB_PATH = Path(_APP_DB_TEMP_DIR.name) / "app-import.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_APP_DB_PATH.as_posix()}"
os.environ["SURVEY_START_FOLLOWUP_POLL_SECONDS"] = "3600"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.models import Base, Question, Survey, User
import app.ai_growth.models  # noqa: F401 - register additive tables
import app.ai_growth.prediction as prediction


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)
from app.ai_growth.llm import LLMCallResult


def make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def seed(db):
    publisher = User(email="pub@example.com", password="x", username="pub", created_at=utcnow())
    user1 = User(
        email="u1@example.com",
        password="x",
        username="u1",
        age_range="18-24",
        education_level="Bachelor",
        field="CS",
        status="student",
        state="CA",
        language="English",
        device_type="mobile",
        created_at=utcnow() - timedelta(days=10),
    )
    user2 = User(
        email="u2@example.com",
        password="x",
        username="u2",
        age_range="25-34",
        education_level="Master",
        field="Psychology",
        status="student",
        state="NY",
        language="English",
        device_type="desktop",
        created_at=utcnow() - timedelta(days=2),
    )
    db.add_all([publisher, user1, user2])
    db.commit()

    s1 = Survey(
        publisher_id=publisher.id,
        title="High LLM survey",
        description="Short built-in survey",
        form_url="__builtin__",
        task_type="survey",
        category="research",
        estimated_time=5,
        reward_amount=2.5,
        target_responses=100,
        status="published",
        published_at=utcnow() - timedelta(days=30),
        target_language="English",
    )
    s2 = Survey(
        publisher_id=publisher.id,
        title="Low LLM survey",
        description="Longer external survey",
        form_url="https://example.com",
        task_type="survey",
        category="market",
        estimated_time=30,
        reward_amount=1.0,
        target_responses=50,
        status="published",
        published_at=utcnow(),
        target_language="English",
    )
    db.add_all([s1, s2])
    db.commit()
    db.add(Question(survey_id=s1.id, question_text="How often?", question_type="single", options=["A", "B"], order_index=1))
    db.commit()
    return publisher, user1, user2, s1, s2


def test_prediction_core(db, user1, user2, s1, s2):
    def fake_one(payload):
        assert payload["task"] == "predict_completion_for_one_participant"
        return LLMCallResult(data={
            "completion_probability": 0.82,
            "confidence": "high",
            "top_reasons": ["short task"],
            "risk_reasons": [],
            "recommended_action": "show near top",
            "ranking_note": "good fit",
        })

    prediction.predict_one_with_claude = fake_one
    single = prediction.predict_user_for_survey(db, s1, user1, use_cache=False)
    assert single["completion_probability"] == 0.82
    assert single["llm_ok"] is True

    cached = prediction.predict_user_for_survey(db, s1, user1, use_cache=True)
    assert cached["cached"] is True
    assert cached["completion_probability"] == 0.82

    def fake_participants(payload):
        return LLMCallResult(data={
            "predictions": [
                {
                    "participant_id": item["participant_id"],
                    "completion_probability": 0.7 if item["participant_id"] == user1.id else 0.3,
                    "confidence": "medium",
                    "top_reasons": ["mock reason"],
                    "risk_reasons": [],
                    "recommended_action": "invite",
                    "ranking_note": "batch",
                }
                for item in payload["participants"]
            ]
        })

    prediction.predict_participants_with_claude = fake_participants
    scored = prediction.predict_users_for_survey(db, s1, [user1, user2], use_cache=False)
    assert len(scored) == 2
    assert scored[0]["completion_probability"] == 0.7

    def fake_rank_surveys(payload):
        return LLMCallResult(data={
            "recommendations": [
                {
                    "survey_id": item["survey_id"],
                    "completion_probability": 0.9 if item["survey_id"] == s1.id else 0.2,
                    "confidence": "high",
                    "top_reasons": ["fit"],
                    "risk_reasons": [],
                    "recommended_action": "recommend",
                    "ranking_note": "ranked",
                }
                for item in payload["surveys"]
            ]
        })

    prediction.rank_surveys_with_claude = fake_rank_surveys
    recs = prediction.recommend_surveys_for_user(db, [s1, s2], user1, use_cache=False)
    assert recs[s1.id]["completion_probability"] == 0.9
    assert recs[s2.id]["completion_probability"] == 0.2

    def fake_summary(payload):
        return LLMCallResult(data={
            "completion_probability": 0.75,
            "confidence": "medium",
            "segment_label": "Students",
            "top_reasons": ["short"],
            "risk_reasons": ["external risk"],
            "recommended_action": "raise visibility",
            "audience_strategy": "target active users",
        })

    prediction.summarize_survey_with_claude = fake_summary
    summary = prediction.survey_prediction_summary(db, s1, force=True)
    assert summary["completion_probability"] == 0.75
    assert summary["recommended_action"] == "raise visibility"

    def fake_preview(payload):
        return LLMCallResult(data={
            "completion_probability": 0.61,
            "confidence": "low",
            "segment_label": "Preview",
            "top_reasons": ["clear"],
            "risk_reasons": ["small sample"],
            "recommended_action": "improve desc",
            "audience_strategy": "broad",
        })

    prediction.preview_survey_with_claude = fake_preview
    preview = prediction.preview_summary_from_payload(db, {"title": "Draft", "description": "x", "estimated_time": 3, "reward_amount": 1, "target_responses": 5})
    assert preview["completion_probability"] == 0.61
    assert preview["llm_ok"] is True


def test_dashboard_routes(db, user1, s1, s2):
    import api.main as main

    def override_db():
        yield db

    def fake_user():
        return user1

    def fake_recommend(db_arg, surveys, user_arg, use_cache=True):
        return {
            survey.id: {
                "completion_probability": 0.9 if survey.id == s1.id else 0.1,
                "confidence": "high",
                "top_reasons": ["mock"],
                "risk_reasons": [],
                "recommended_action": "mock",
                "ranking_note": "mock",
                "model_version": "test",
            }
            for survey in surveys
        }

    main.app.dependency_overrides[main.get_db] = override_db
    main.app.dependency_overrides[main.get_current_user] = fake_user
    main.recommend_surveys_for_user = fake_recommend

    client = TestClient(main.app)
    for path in ["/dashboard", "/dashboard/mobile"]:
        response = client.get(path)
        assert response.status_code == 200
        high_pos = response.text.find("High LLM survey")
        low_pos = response.text.find("Low LLM survey")
        assert high_pos != -1 and low_pos != -1 and high_pos < low_pos


def main():
    db = make_db()
    _, user1, user2, s1, s2 = seed(db)
    test_prediction_core(db, user1, user2, s1, s2)
    test_dashboard_routes(db, user1, s1, s2)
    print("LLM-only smoke tests passed.")


if __name__ == "__main__":
    main()
