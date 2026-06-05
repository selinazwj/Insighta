"""LLM-only completion prediction and recommendation for Insighta.

This file replaces the original rule-weighted V0 predictor. The only model that
assigns completion probabilities or recommendation ranking is Claude via the
Anthropic API. Local code is limited to:
- collecting sanitized factual context,
- applying existing hard eligibility filters,
- validating Claude JSON,
- caching Claude outputs, and
- aggregating Claude outputs for dashboards/publisher summaries.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from app.models import Question, Response, Survey, User
from app.ai_growth.matching import survey_match_result
from app.ai_growth.models import JumpEvent, RespondentPrediction, UserActivityEvent
from app.ai_growth.segments import user_segment_key, user_segment_label, rebuild_segment_stats
from app.ai_growth.llm import (
    claude_model_name,
    llm_batch_size,
    llm_cache_hours,
    predict_one_with_claude,
    predict_participants_with_claude,
    preview_survey_with_claude,
    rank_surveys_with_claude,
    safe_survey_payload,
    safe_user_payload,
    summarize_survey_with_claude,
)

MODEL_VERSION = f"claude-llm-only:{claude_model_name()}"


def _clamp_probability(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, str) and value.strip().endswith("%"):
            value = float(value.strip().rstrip("%")) / 100.0
        else:
            value = float(value)
    except Exception:
        value = default
    return round(max(0.0, min(1.0, value)), 4)


def _clean_confidence(value: Any) -> str:
    value = str(value or "low").strip().lower()
    return value if value in {"low", "medium", "high"} else "low"


def _clean_list(value: Any, fallback: Optional[list[str]] = None, limit: int = 5) -> list[str]:
    fallback = fallback or []
    if not isinstance(value, list):
        value = fallback
    cleaned = [str(x).strip() for x in value if str(x).strip()]
    return cleaned[:limit]


def _now() -> datetime:
    return datetime.utcnow()


def _cache_expires_at() -> datetime:
    return _now() + timedelta(hours=llm_cache_hours())


def _chunked(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _safe_rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


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


def _unavailable_prediction(survey: Survey, user: User, error: str) -> dict:
    return {
        "survey_id": getattr(survey, "id", None),
        "participant_id": getattr(user, "id", None),
        "completion_probability": 0.0,
        "confidence": "low",
        "segment_label": user_segment_label(user),
        "top_reasons": [],
        "risk_reasons": [error],
        "recommended_action": "Configure Claude by setting ANTHROPIC_API_KEY and AI_GROWTH_CLAUDE_MODEL, then recompute predictions.",
        "ranking_note": "Claude prediction unavailable; no local rule score was used.",
        "features": {"llm_error": error, "llm_only": True},
        "model_version": MODEL_VERSION,
        "cached": False,
        "llm_ok": False,
    }


def _normalize_llm_prediction(raw: dict, *, survey: Survey, user: User, context: Optional[dict] = None) -> dict:
    context = context or {}
    probability = _clamp_probability(raw.get("completion_probability"), default=0.0)
    result = {
        "survey_id": getattr(survey, "id", None),
        "participant_id": getattr(user, "id", None),
        "completion_probability": probability,
        "confidence": _clean_confidence(raw.get("confidence")),
        "segment_label": raw.get("segment_label") or user_segment_label(user),
        "top_reasons": _clean_list(raw.get("top_reasons"), limit=5),
        "risk_reasons": _clean_list(raw.get("risk_reasons"), limit=5),
        "recommended_action": str(raw.get("recommended_action") or "").strip(),
        "ranking_note": str(raw.get("ranking_note") or "").strip(),
        "features": {
            "llm_only": True,
            "provider": "anthropic",
            "model": claude_model_name(),
            "raw_context_summary": context,
            "llm_output": {
                "recommended_action": str(raw.get("recommended_action") or "").strip(),
                "ranking_note": str(raw.get("ranking_note") or "").strip(),
            },
        },
        "model_version": MODEL_VERSION,
        "cached": False,
        "llm_ok": True,
    }
    if not result["top_reasons"]:
        result["top_reasons"] = ["Claude identified this as a relevant candidate based on the supplied survey and behavior context."]
    if not result["recommended_action"]:
        result["recommended_action"] = "Use this Claude prediction for ranking; collect more completion data to improve future judgments."
    return result


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


def predict_user_for_survey(db: Session, survey: Survey, user: User, use_cache: bool = True) -> dict:
    """Return Claude-only completion prediction for one survey/user pair."""
    if use_cache:
        cached = _get_cached_prediction(db, survey.id, user.id)
        if cached:
            return cached

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


def _extract_items_by_id(data: dict, list_key: str, id_key: str) -> dict[int, dict]:
    items = data.get(list_key) if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    by_id: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            key = int(item.get(id_key))
        except Exception:
            continue
        by_id[key] = item
    return by_id


def predict_users_for_survey(db: Session, survey: Survey, users: list[User], use_cache: bool = True) -> list[dict]:
    """Batch Claude-only predictions for one survey and many users."""
    results_by_user: dict[int, dict] = {}
    missing: list[User] = []

    for user in users:
        cached = _get_cached_prediction(db, survey.id, user.id) if use_cache else None
        if cached:
            results_by_user[user.id] = cached
        else:
            missing.append(user)

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


def top_respondents(db: Session, survey: Survey, limit: int = 20, force: bool = False) -> list[dict]:
    users = candidate_users_for_survey(db, survey)
    scored = predict_users_for_survey(db, survey, users, use_cache=not force)
    scored.sort(key=lambda x: x.get("completion_probability") or 0.0, reverse=True)
    return scored[:limit]


def recommend_surveys_for_user(db: Session, surveys: list[Survey], user: User, use_cache: bool = True) -> dict[int, dict]:
    """Batch Claude-only recommendations for dashboard ordering."""
    recommendations: dict[int, dict] = {}
    missing: list[Survey] = []

    for survey in surveys:
        cached = _get_cached_prediction(db, survey.id, user.id) if use_cache else None
        if cached:
            recommendations[survey.id] = cached
        else:
            missing.append(survey)

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


def _summary_unavailable(survey: Survey, error: str, candidate_count: int = 0) -> dict:
    return {
        "survey_id": getattr(survey, "id", None),
        "model_version": MODEL_VERSION,
        "candidate_count": candidate_count,
        "completion_probability": 0.0,
        "confidence": "low",
        "segment_label": "Claude unavailable",
        "top_reasons": [],
        "risk_reasons": [error],
        "recommended_action": "Configure Claude by setting ANTHROPIC_API_KEY and AI_GROWTH_CLAUDE_MODEL, then recompute predictions.",
        "audience_strategy": "No local rule summary was generated.",
        "llm_ok": False,
    }


def survey_prediction_summary(db: Session, survey: Survey, force: bool = False) -> dict:
    candidates = candidate_users_for_survey(db, survey)
    predictions = predict_users_for_survey(db, survey, candidates, use_cache=not force)
    if not predictions:
        return _summary_unavailable(survey, "No candidate respondents are available for Claude to evaluate.", candidate_count=0)

    probs = [float(p.get("completion_probability") or 0.0) for p in predictions]
    avg_prob = round(sum(probs) / len(probs), 4) if probs else 0.0
    top_probs = sorted(probs, reverse=True)[: max(1, min(10, len(probs)))]
    top_avg = round(sum(top_probs) / len(top_probs), 4) if top_probs else 0.0

    segment_scores: dict[str, list[float]] = {}
    segment_labels: dict[str, str] = {}
    reason_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}

    for prediction in predictions:
        user = db.query(User).filter(User.id == prediction.get("participant_id")).first()
        if user:
            key = user_segment_key(user)
            segment_scores.setdefault(key, []).append(float(prediction.get("completion_probability") or 0.0))
            segment_labels[key] = user_segment_label(user)
        for reason in prediction.get("top_reasons", []):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for risk in prediction.get("risk_reasons", []):
            risk_counts[risk] = risk_counts.get(risk, 0) + 1

    segment_summary = []
    for key, scores in segment_scores.items():
        segment_summary.append({
            "segment_key": key,
            "segment_label": segment_labels.get(key, key),
            "candidate_count": len(scores),
            "avg_probability": round(sum(scores) / len(scores), 4),
        })
    segment_summary.sort(key=lambda x: (x["avg_probability"], x["candidate_count"]), reverse=True)

    preliminary_summary = {
        "survey_id": survey.id,
        "model_version": MODEL_VERSION,
        "candidate_count": len(predictions),
        "completion_probability": avg_prob,
        "top_candidate_probability": top_avg,
        "confidence": "high" if len(predictions) >= 30 else "medium" if len(predictions) >= 10 else "low",
        "segment_label": segment_summary[0]["segment_label"] if segment_summary else "General respondents",
        "top_segments": segment_summary[:5],
        "funnel_segments": rebuild_segment_stats(db, survey)[:5],
        "top_reasons": [k for k, _ in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:5]],
        "risk_reasons": [k for k, _ in sorted(risk_counts.items(), key=lambda x: x[1], reverse=True)[:5]],
        "high_probability_count": sum(1 for p in probs if p >= 0.7),
        "medium_probability_count": sum(1 for p in probs if 0.45 <= p < 0.7),
        "low_probability_count": sum(1 for p in probs if p < 0.45),
        "llm_ok": all(bool(p.get("llm_ok")) for p in predictions),
    }

    payload = {
        "task": "summarize_survey_prediction",
        "survey": safe_survey_payload(survey),
        "survey_context": survey_context_features(db, survey),
        "aggregated_claude_predictions": preliminary_summary,
        "top_prediction_examples": sorted(
            [
                {
                    "participant_id": p.get("participant_id"),
                    "completion_probability": p.get("completion_probability"),
                    "confidence": p.get("confidence"),
                    "top_reasons": p.get("top_reasons", [])[:3],
                    "risk_reasons": p.get("risk_reasons", [])[:3],
                }
                for p in predictions
            ],
            key=lambda x: x.get("completion_probability") or 0.0,
            reverse=True,
        )[:10],
    }
    llm_summary = summarize_survey_with_claude(payload)
    if llm_summary.error or not llm_summary.data:
        preliminary_summary["recommended_action"] = "Claude participant predictions were generated, but Claude summary generation failed. Review top respondent reasons directly."
        preliminary_summary["audience_strategy"] = llm_summary.error or "Claude returned no summary data."
        return preliminary_summary

    data = llm_summary.data
    preliminary_summary["completion_probability"] = _clamp_probability(data.get("completion_probability"), avg_prob)
    preliminary_summary["confidence"] = _clean_confidence(data.get("confidence") or preliminary_summary["confidence"])
    preliminary_summary["segment_label"] = str(data.get("segment_label") or preliminary_summary["segment_label"])
    preliminary_summary["top_reasons"] = _clean_list(data.get("top_reasons"), preliminary_summary.get("top_reasons", []), limit=5)
    preliminary_summary["risk_reasons"] = _clean_list(data.get("risk_reasons"), preliminary_summary.get("risk_reasons", []), limit=5)
    preliminary_summary["recommended_action"] = str(data.get("recommended_action") or "").strip()
    preliminary_summary["audience_strategy"] = str(data.get("audience_strategy") or "").strip()
    return preliminary_summary


def preview_summary_from_payload(db: Session, payload: dict) -> dict:
    """LLM-only preview for unsaved survey draft payloads."""
    survey = SimpleNamespace(**payload)
    survey.id = int(payload.get("id") or 0)
    survey.publisher_id = int(payload.get("publisher_id") or 0)
    survey.title = payload.get("title") or "Preview survey"
    survey.description = payload.get("description") or ""
    survey.form_url = payload.get("form_url") or "__builtin__"
    survey.task_type = payload.get("task_type") or "survey"
    survey.category = payload.get("category") or "research"
    survey.estimated_time = int(payload.get("estimated_time") or 10)
    survey.reward_amount = float(payload.get("reward_amount") or payload.get("per_person_gross") or 0.0)
    survey.target_responses = int(payload.get("target_responses") or 50)
    survey.current_responses = int(payload.get("current_responses") or 0)
    survey.status = payload.get("status") or "draft"
    survey.published_at = None

    users = db.query(User).order_by(User.created_at.desc()).limit(80).all()
    participant_sample = [
        {
            "participant_id": user.id,
            "profile": safe_user_payload(user),
            "behavior_overall": user_history_features(db, user, None),
            "eligibility_context": _eligibility_context(survey, user),
            "segment_label": user_segment_label(user),
        }
        for user in users
    ]

    llm_result = preview_survey_with_claude({
        "task": "preview_unsaved_survey",
        "survey_draft": safe_survey_payload(survey),
        "candidate_sample_count": len(participant_sample),
        "candidate_sample": participant_sample,
        "model_policy": "Claude is the only preview model. No local weighted rule score is provided.",
    })
    if llm_result.error or not llm_result.data:
        return {
            "survey_id": None,
            "model_version": MODEL_VERSION,
            "candidate_count": len(users),
            "completion_probability": 0.0,
            "confidence": "low",
            "segment_label": "Claude unavailable",
            "top_reasons": [],
            "risk_reasons": [llm_result.error or "Claude returned no preview data."],
            "recommended_action": "Set ANTHROPIC_API_KEY and AI_GROWTH_CLAUDE_MODEL to enable LLM-only preview.",
            "audience_strategy": "No local rule preview was generated.",
            "llm_ok": False,
        }

    data = llm_result.data
    return {
        "survey_id": None,
        "model_version": MODEL_VERSION,
        "candidate_count": len(users),
        "completion_probability": _clamp_probability(data.get("completion_probability"), 0.0),
        "confidence": _clean_confidence(data.get("confidence")),
        "segment_label": str(data.get("segment_label") or "Preview audience"),
        "top_reasons": _clean_list(data.get("top_reasons"), limit=5),
        "risk_reasons": _clean_list(data.get("risk_reasons"), limit=5),
        "recommended_action": str(data.get("recommended_action") or "").strip(),
        "audience_strategy": str(data.get("audience_strategy") or "").strip(),
        "llm_ok": True,
    }


def recommended_action(summary: dict) -> str:
    """Backward-compatible helper for old callers.

    The old implementation generated a rule-based action. This version simply
    returns the Claude-generated action if present, otherwise a neutral message.
    """
    return str(summary.get("recommended_action") or "Review the Claude-generated prediction and recommendation details.").strip()
