"""Explicit single and batch participant completion predictions."""
from __future__ import annotations

from typing import Optional
from sqlalchemy.orm import Session
from app.models import Survey
from app.models import User
from app.ai_growth.llm import llm_batch_size
import os
from app.ai_growth.llm import predict_one_with_claude
from app.ai_growth.llm import predict_participants_with_claude
from app.ai_growth.matching import survey_match_result

from .common import (
    _chunked,
)
from .normalization import (
    _extract_items_by_id,
    _normalize_llm_prediction,
    _unavailable_prediction,
)
from .context import (
    _participant_batch_payload,
    _single_prediction_payload,
)
from .cache import (
    _get_cached_prediction,
    _save_prediction_cache,
)

def predict_user_for_survey(
    db: Session,
    survey: Survey,
    user: User,
    use_cache: bool = True,
    allow_llm: bool = True,
) -> dict:
    """Return Claude-only completion prediction for one survey/user pair."""
    if use_cache:
        cached = _get_cached_prediction(db, survey.id, user.id)
        if cached:
            return cached
    if not allow_llm:
        return _unavailable_prediction(survey, user, "No cached AI prediction is available. Request an explicit AI analysis.")

    payload = _single_prediction_payload(db, survey, user)
    llm_result = predict_one_with_claude(payload)
    if llm_result.error or not llm_result.data:
        return _unavailable_prediction(survey, user, llm_result.error or "Claude returned no prediction data.")

    raw = llm_result.data.get("prediction") if isinstance(llm_result.data.get("prediction"), dict) else llm_result.data
    result = _normalize_llm_prediction(
        raw,
        survey=survey,
        user=user,
        context={
            "call_type": "single",
            "eligibility": payload.get("eligibility_context"),
            "survey_context": payload.get("survey_context"),
            "participant_behavior": payload.get("participant_behavior"),
        },
    )
    _save_prediction_cache(db, result)
    return result

def candidate_users_for_survey(db: Session, survey: Survey, limit_pool: Optional[int] = None) -> list[User]:
    """Return locally eligible candidates only; no ranking is performed here."""
    limit_pool = limit_pool or int(os.environ.get("AI_GROWTH_LLM_CANDIDATE_LIMIT", "120"))
    users = db.query(User).filter(User.id != survey.publisher_id).order_by(User.created_at.desc()).limit(limit_pool).all()
    eligible = [user for user in users if survey_match_result(survey, user, strict=True).eligible]
    return eligible or users

def predict_users_for_survey(
    db: Session,
    survey: Survey,
    users: list[User],
    use_cache: bool = True,
    allow_llm: bool = True,
) -> list[dict]:
    """Batch Claude-only predictions for one survey and many users."""
    results_by_user: dict[int, dict] = {}
    missing: list[User] = []

    for user in users:
        cached = _get_cached_prediction(db, survey.id, user.id) if use_cache else None
        if cached:
            results_by_user[user.id] = cached
        else:
            missing.append(user)

    if not allow_llm:
        return [results_by_user[user.id] for user in users if user.id in results_by_user]

    for batch in _chunked(missing, llm_batch_size()):
        payload = _participant_batch_payload(db, survey, batch)
        llm_result = predict_participants_with_claude(payload)
        if llm_result.error or not llm_result.data:
            for user in batch:
                results_by_user[user.id] = _unavailable_prediction(survey, user, llm_result.error or "Claude returned no batch prediction data.")
            continue

        raw_by_id = _extract_items_by_id(llm_result.data, "predictions", "participant_id")
        for user in batch:
            raw = raw_by_id.get(user.id)
            if not raw:
                results_by_user[user.id] = _unavailable_prediction(survey, user, "Claude did not return a prediction for this participant_id.")
                continue
            result = _normalize_llm_prediction(
                raw,
                survey=survey,
                user=user,
                context={"call_type": "participant_batch", "batch_size": len(batch)},
            )
            _save_prediction_cache(db, result)
            results_by_user[user.id] = result

    return [results_by_user[user.id] for user in users if user.id in results_by_user]

def top_respondents(
    db: Session,
    survey: Survey,
    limit: int = 20,
    force: bool = False,
    candidate_limit: Optional[int] = None,
    allow_llm: bool = True,
) -> list[dict]:
    users = candidate_users_for_survey(db, survey, limit_pool=candidate_limit)
    scored = predict_users_for_survey(db, survey, users, use_cache=not force, allow_llm=allow_llm)
    scored.sort(key=lambda x: x.get("completion_probability") or 0.0, reverse=True)
    return scored[:limit]

def recommended_action(summary: dict) -> str:
    """Backward-compatible helper for old callers.

    The old implementation generated a rule-based action. This version simply
    returns the Claude-generated action if present, otherwise a neutral message.
    """
    return str(summary.get("recommended_action") or "Review the Claude-generated prediction and recommendation details.").strip()
