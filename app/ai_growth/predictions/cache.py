"""Prediction persistence and cache-only dashboard reads."""
from __future__ import annotations

from typing import Optional
from app.ai_growth.models import RespondentPrediction
from sqlalchemy.orm import Session
from app.models import Survey
from app.models import User

from .config import (
    MODEL_VERSION,
)
from .common import (
    _cache_expires_at,
    _now,
)

def _prediction_from_cache(cached: RespondentPrediction) -> dict:
    features = cached.features_json or {}
    llm_output = features.get("llm_output") or {}
    return {
        "survey_id": cached.survey_id,
        "participant_id": cached.participant_id,
        "completion_probability": round(cached.probability, 4),
        "confidence": cached.confidence,
        "segment_label": cached.segment_label,
        "top_reasons": cached.reasons_json or [],
        "risk_reasons": cached.risk_json or [],
        "recommended_action": llm_output.get("recommended_action") or "",
        "ranking_note": llm_output.get("ranking_note") or "",
        "features": features,
        "model_version": cached.model_version,
        "cached": True,
        "llm_ok": True,
    }

def _get_cached_prediction(db: Session, survey_id: int, participant_id: int) -> Optional[dict]:
    cached = db.query(RespondentPrediction).filter(
        RespondentPrediction.survey_id == survey_id,
        RespondentPrediction.participant_id == participant_id,
        RespondentPrediction.model_version == MODEL_VERSION,
        RespondentPrediction.expires_at > _now(),
    ).first()
    return _prediction_from_cache(cached) if cached else None

def _cached_predictions_for_user(
    db: Session,
    survey_ids: list[int],
    participant_id: int,
) -> dict[int, dict]:
    if not survey_ids:
        return {}
    rows = db.query(RespondentPrediction).filter(
        RespondentPrediction.survey_id.in_(survey_ids),
        RespondentPrediction.participant_id == participant_id,
        RespondentPrediction.model_version == MODEL_VERSION,
        RespondentPrediction.expires_at > _now(),
    ).all()
    return {row.survey_id: _prediction_from_cache(row) for row in rows}

def cached_recommendations_for_user(db: Session, surveys: list[Survey], user: User) -> dict[int, dict]:
    """Return dashboard recommendation cache only; never calls Claude."""
    return _cached_predictions_for_user(
        db,
        [survey.id for survey in surveys],
        user.id,
    )

def _save_prediction_cache(db: Session, result: dict) -> None:
    if not result.get("llm_ok"):
        return
    survey_id = result.get("survey_id")
    participant_id = result.get("participant_id")
    if survey_id is None or participant_id is None:
        return

    existing = db.query(RespondentPrediction).filter(
        RespondentPrediction.survey_id == survey_id,
        RespondentPrediction.participant_id == participant_id,
        RespondentPrediction.model_version == MODEL_VERSION,
    ).first()
    if not existing:
        existing = RespondentPrediction(
            survey_id=survey_id,
            participant_id=participant_id,
            model_version=MODEL_VERSION,
        )
        db.add(existing)

    existing.probability = result["completion_probability"]
    existing.confidence = result["confidence"]
    existing.segment_label = result["segment_label"]
    existing.reasons_json = result.get("top_reasons") or []
    existing.risk_json = result.get("risk_reasons") or []
    existing.features_json = result.get("features") or {}
    existing.created_at = _now()
    existing.expires_at = _cache_expires_at()
    db.commit()
