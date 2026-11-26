"""Explicit survey recommendation ranking."""
from __future__ import annotations

from sqlalchemy.orm import Session
from app.models import Survey
from app.models import User
from app.ai_growth.llm import llm_batch_size
from app.ai_growth.llm import rank_surveys_with_claude

from .common import (
    _chunked,
)
from .normalization import (
    _extract_items_by_id,
    _normalize_llm_prediction,
    _unavailable_prediction,
)
from .context import (
    _survey_batch_payload,
)
from .cache import (
    _cached_predictions_for_user,
    _save_prediction_cache,
)

def recommend_surveys_for_user(db: Session, surveys: list[Survey], user: User, use_cache: bool = True) -> dict[int, dict]:
    """Batch Claude-only recommendations for dashboard ordering."""
    recommendations = (
        _cached_predictions_for_user(db, [survey.id for survey in surveys], user.id)
        if use_cache else {}
    )
    missing = [survey for survey in surveys if survey.id not in recommendations]

    for batch in _chunked(missing, llm_batch_size()):
        payload = _survey_batch_payload(db, batch, user)
        llm_result = rank_surveys_with_claude(payload)
        if llm_result.error or not llm_result.data:
            for survey in batch:
                recommendations[survey.id] = _unavailable_prediction(survey, user, llm_result.error or "Claude returned no survey recommendation data.")
            continue

        raw_by_id = _extract_items_by_id(llm_result.data, "recommendations", "survey_id")
        for survey in batch:
            raw = raw_by_id.get(survey.id)
            if not raw:
                recommendations[survey.id] = _unavailable_prediction(survey, user, "Claude did not return a recommendation for this survey_id.")
                continue
            result = _normalize_llm_prediction(
                raw,
                survey=survey,
                user=user,
                context={"call_type": "survey_batch", "batch_size": len(batch)},
            )
            _save_prediction_cache(db, result)
            recommendations[survey.id] = result

    return recommendations
