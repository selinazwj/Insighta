"""Sanitized participant, survey, eligibility, and behavior context."""
from __future__ import annotations

from app.ai_growth.models import JumpEvent
from typing import Optional
from app.models import Question
from app.models import Response
from sqlalchemy.orm import Session
from app.models import Survey
from app.models import User
from app.ai_growth.models import UserActivityEvent
from app.ai_growth.llm import safe_survey_payload
from app.ai_growth.llm import safe_user_payload
from app.ai_growth.matching import survey_match_result
from datetime import timedelta
from app.ai_growth.segments import user_segment_label

from .common import (
    _now,
    _safe_rate,
)

def user_history_features(db: Session, user: User, survey: Optional[Survey] = None) -> dict:
    """Raw behavior facts for Claude. No rule score is computed here."""
    started = db.query(Response).filter(Response.participant_id == user.id).count()
    completed = db.query(Response).filter(
        Response.participant_id == user.id,
        Response.status == "completed",
    ).count()
    rejected = db.query(Response).filter(
        Response.participant_id == user.id,
        Response.status == "rejected",
    ).count()

    recent_completed = db.query(Response).filter(
        Response.participant_id == user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= _now() - timedelta(days=30),
    ).count()

    recent_activity_events = db.query(UserActivityEvent).filter(
        UserActivityEvent.user_id == user.id,
        UserActivityEvent.created_at >= _now() - timedelta(days=30),
    ).count()

    category_started = 0
    category_completed = 0
    if survey is not None and getattr(survey, "category", None):
        joined = db.query(Response).join(Survey, Survey.id == Response.survey_id).filter(
            Response.participant_id == user.id,
            Survey.category == survey.category,
        )
        category_started = joined.count()
        category_completed = joined.filter(Response.status == "completed").count()

    completed_responses = db.query(Response).filter(
        Response.participant_id == user.id,
        Response.status == "completed",
        Response.started_at.isnot(None),
        Response.completed_at.isnot(None),
    ).order_by(Response.completed_at.desc()).limit(50).all()

    durations: list[float] = []
    for response in completed_responses:
        try:
            durations.append(max(0.0, (response.completed_at - response.started_at).total_seconds() / 60.0))
        except Exception:
            pass

    return {
        "started_total": started,
        "completed_total": completed,
        "rejected_total": rejected,
        "completion_rate_observed": _safe_rate(completed, started),
        "rejection_rate_observed": _safe_rate(rejected, started),
        "recent_completed_30d": recent_completed,
        "recent_activity_events_30d": recent_activity_events,
        "category_started_total": category_started,
        "category_completed_total": category_completed,
        "category_completion_rate_observed": _safe_rate(category_completed, category_started),
        "avg_completion_minutes_observed": round(sum(durations) / len(durations), 2) if durations else None,
    }

def survey_context_features(db: Session, survey: Survey) -> dict:
    """Raw survey/funnel facts for Claude. No rule score is computed here."""
    started = db.query(Response).filter(Response.survey_id == survey.id).count()
    completed = db.query(Response).filter(
        Response.survey_id == survey.id,
        Response.status == "completed",
    ).count()
    rejected = db.query(Response).filter(
        Response.survey_id == survey.id,
        Response.status == "rejected",
    ).count()

    clicked = db.query(JumpEvent).filter(JumpEvent.survey_id == survey.id).count()
    returned = db.query(JumpEvent).filter(
        JumpEvent.survey_id == survey.id,
        JumpEvent.returned_at.isnot(None),
    ).count()
    jump_completed = db.query(JumpEvent).filter(
        JumpEvent.survey_id == survey.id,
        JumpEvent.status == "completed",
    ).count()

    question_count = 0
    question_preview = []
    if (getattr(survey, "form_url", None) or "") == "__builtin__":
        questions = db.query(Question).filter(Question.survey_id == survey.id).order_by(Question.order_index.asc()).limit(8).all()
        question_count = db.query(Question).filter(Question.survey_id == survey.id).count()
        question_preview = [
            {
                "order": getattr(q, "order_index", None),
                "type": getattr(q, "question_type", None),
                "required": bool(getattr(q, "is_required", True)),
                "text_preview": (getattr(q, "question_text", "") or "")[:160],
            }
            for q in questions
        ]

    estimated_time = getattr(survey, "estimated_time", None) or 0
    reward = getattr(survey, "reward_amount", None) or 0
    try:
        reward_per_minute = round(float(reward) / max(1, int(estimated_time)), 4)
    except Exception:
        reward_per_minute = None

    return {
        "responses_started_total": started,
        "responses_completed_total": completed,
        "responses_rejected_total": rejected,
        "observed_completion_rate": _safe_rate(completed, started),
        "target_responses": getattr(survey, "target_responses", None),
        "current_responses_field": getattr(survey, "current_responses", None),
        "jump_clicks_total": clicked,
        "jump_returns_total": returned,
        "jump_completed_total": jump_completed,
        "observed_jump_return_rate": _safe_rate(returned, clicked),
        "question_count": question_count,
        "question_preview": question_preview,
        "reward_per_minute_raw": reward_per_minute,
    }

def _eligibility_context(survey: Survey, user: User) -> dict:
    match = survey_match_result(survey, user, strict=True)
    return {
        "eligible": match.eligible,
        "matched_fields": match.matched_fields,
        "missing_fields": match.missing_fields,
        "failed_fields": match.failed_fields,
        "note": "Eligibility is a local hard filter only; it is not a prediction score.",
    }

def _single_prediction_payload(db: Session, survey: Survey, user: User) -> dict:
    return {
        "task": "predict_completion_for_one_participant",
        "model_policy": "Claude is the only probability/recommendation model. No local weighted rule score is provided.",
        "survey": safe_survey_payload(survey),
        "survey_context": survey_context_features(db, survey),
        "participant_id": user.id,
        "participant_profile": safe_user_payload(user),
        "participant_behavior": user_history_features(db, user, survey),
        "eligibility_context": _eligibility_context(survey, user),
    }

def _participant_batch_payload(db: Session, survey: Survey, users: list[User]) -> dict:
    return {
        "task": "rank_participants_for_one_survey",
        "model_policy": "Claude is the only probability/recommendation model. No local weighted rule score is provided.",
        "survey": safe_survey_payload(survey),
        "survey_context": survey_context_features(db, survey),
        "participants": [
            {
                "participant_id": user.id,
                "profile": safe_user_payload(user),
                "behavior": user_history_features(db, user, survey),
                "eligibility_context": _eligibility_context(survey, user),
                "segment_label": user_segment_label(user),
            }
            for user in users
        ],
    }

def _survey_batch_payload(db: Session, surveys: list[Survey], user: User) -> dict:
    return {
        "task": "rank_surveys_for_one_participant",
        "model_policy": "Claude is the only probability/recommendation model. No urgency/date/rule ranking score is provided.",
        "participant_id": user.id,
        "participant_profile": safe_user_payload(user),
        "participant_behavior_overall": user_history_features(db, user, None),
        "surveys": [
            {
                "survey_id": survey.id,
                "survey": safe_survey_payload(survey),
                "survey_context": survey_context_features(db, survey),
                "participant_behavior_for_category": user_history_features(db, user, survey),
                "eligibility_context": _eligibility_context(survey, user),
            }
            for survey in surveys
        ],
    }
