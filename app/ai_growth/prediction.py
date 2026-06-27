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

from collections import Counter
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
    value = {"l": "low", "m": "medium", "h": "high"}.get(value, value)
    return value if value in {"low", "medium", "high"} else "low"


def _clean_list(value: Any, fallback: Optional[list[str]] = None, limit: int = 5) -> list[str]:
    fallback = fallback or []
    if not isinstance(value, list):
        value = fallback
    cleaned = [str(x).strip() for x in value if str(x).strip()]
    return cleaned[:limit]


POSITIVE_REASON_TEXT = {
    "short_task": "The task is short enough to reduce completion friction.",
    "fair_reward": "The reward is reasonable for the expected effort.",
    "device_fit": "The participant's device and participation format fit the task.",
    "strong_history": "Observed completion history suggests reliable follow-through.",
    "category_fit": "Past activity indicates familiarity with this survey category.",
    "clear_task": "The study description and requirements are relatively clear.",
    "builtin_flow": "The built-in flow avoids external return-tracking friction.",
    "active_user": "Recent platform activity suggests the participant is engaged.",
}

RISK_REASON_TEXT = {
    "long_task": "The task length may increase abandonment risk.",
    "low_reward": "The reward may be low relative to the expected effort.",
    "external_friction": "An external flow may add return-tracking or handoff friction.",
    "sparse_history": "There is limited behavioral history for a confident estimate.",
    "device_mismatch": "The participant's device may not fit the task well.",
    "unclear_task": "The study description or requirements may be unclear.",
    "narrow_fit": "The eligible audience appears narrow, limiting robust evidence.",
}


def _expand_reason_codes(value: Any, *, risk: bool = False, limit: int = 3) -> list[str]:
    mapping = RISK_REASON_TEXT if risk else POSITIVE_REASON_TEXT
    expanded: list[str] = []
    for item in _clean_list(value, limit=limit):
        expanded.append(mapping.get(item, item.replace("_", " ").strip()))
    return expanded


def _compact_facts(data: dict) -> dict:
    """Drop fields that carry no evidence while preserving numeric zero/False."""
    result: dict = {}
    for key, value in data.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            value = _compact_facts(value)
            if not value:
                continue
        result[key] = value
    return result


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
        questions = db.query(Question).filter(Question.survey_id == survey.id).order_by(Question.order_index.asc()).limit(4).all()
        question_count = db.query(Question).filter(Question.survey_id == survey.id).count()
        question_preview = [
            {
                "order": getattr(q, "order_index", None),
                "type": getattr(q, "question_type", None),
                "required": bool(getattr(q, "is_required", True)),
                "text_preview": (getattr(q, "question_text", "") or "")[:96],
            }
            for q in questions
        ]

    estimated_time = getattr(survey, "estimated_time", None) or 0
    reward = getattr(survey, "reward_amount", None) or 0
    try:
        reward_per_minute = round(float(reward) / max(1, int(estimated_time)), 4)
    except Exception:
        reward_per_minute = None

    return _compact_facts({
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
    })


def _eligibility_context(survey: Survey, user: User) -> dict:
    match = survey_match_result(survey, user, strict=True)
    # Eligible candidates do not need every matched field repeated in the LLM
    # payload. Only exceptions carry extra evidence.
    return _compact_facts({
        "eligible": match.eligible,
        "missing_fields": match.missing_fields if not match.eligible else [],
        "failed_fields": match.failed_fields if not match.eligible else [],
    })


def _history_payload(features: dict, *, category_only: bool = False) -> dict:
    if category_only:
        return _compact_facts({
            "category_started": features.get("category_started_total"),
            "category_completed": features.get("category_completed_total"),
        })
    return _compact_facts({
        "started": features.get("started_total"),
        "completed": features.get("completed_total"),
        "rejected": features.get("rejected_total"),
        "recent_completed_30d": features.get("recent_completed_30d"),
        "activity_30d": features.get("recent_activity_events_30d"),
        "category_started": features.get("category_started_total"),
        "category_completed": features.get("category_completed_total"),
        "avg_minutes": features.get("avg_completion_minutes_observed"),
    })


def _survey_context_payload(features: dict) -> dict:
    return _compact_facts({
        "started": features.get("responses_started_total"),
        "completed": features.get("responses_completed_total"),
        "rejected": features.get("responses_rejected_total"),
        "target": features.get("target_responses"),
        "current": features.get("current_responses_field"),
        "jump_clicks": features.get("jump_clicks_total"),
        "jump_returns": features.get("jump_returns_total"),
        "jump_completed": features.get("jump_completed_total"),
        "question_count": features.get("question_count"),
        "question_preview": features.get("question_preview"),
        "reward_per_minute": features.get("reward_per_minute_raw"),
    })


def _candidate_payload(survey: Survey, user: User, behavior: dict) -> dict:
    item = {
        "participant_id": user.id,
        "profile": safe_user_payload(user),
        "behavior": _history_payload(behavior),
    }
    eligibility = _eligibility_context(survey, user)
    if not eligibility.get("eligible"):
        item["eligibility_exception"] = eligibility
    return item


def _survey_candidate_payload(db: Session, survey: Survey, user: User) -> dict:
    history = user_history_features(db, user, survey)
    item = {
        "survey_id": survey.id,
        "survey": safe_survey_payload(survey),
        "survey_context": _survey_context_payload(survey_context_features(db, survey)),
        "category_history": _history_payload(history, category_only=True),
    }
    eligibility = _eligibility_context(survey, user)
    if not eligibility.get("eligible"):
        item["eligibility_exception"] = eligibility
    return item


def _single_prediction_payload(db: Session, survey: Survey, user: User) -> dict:
    history = user_history_features(db, user, survey)
    return {
        "task": "predict_completion_for_one_participant",
        "survey": safe_survey_payload(survey),
        "survey_context": _survey_context_payload(survey_context_features(db, survey)),
        "participant_id": user.id,
        "participant_profile": safe_user_payload(user),
        "participant_behavior": _history_payload(history),
        "eligibility_context": _eligibility_context(survey, user),
    }


def _participant_batch_payload(db: Session, survey: Survey, users: list[User]) -> dict:
    return {
        "task": "rank_participants_for_one_survey",
        "survey": safe_survey_payload(survey),
        "survey_context": _survey_context_payload(survey_context_features(db, survey)),
        "participants": [
            _candidate_payload(survey, user, user_history_features(db, user, survey))
            for user in users
        ],
    }


def _survey_batch_payload(db: Session, surveys: list[Survey], user: User) -> dict:
    overall_history = user_history_features(db, user, None)
    return {
        "task": "rank_surveys_for_one_participant",
        "participant_id": user.id,
        "participant_profile": safe_user_payload(user),
        "participant_behavior": _history_payload(overall_history),
        "surveys": [_survey_candidate_payload(db, survey, user) for survey in surveys],
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
    probability = _clamp_probability(raw.get("completion_probability", raw.get("p")), default=0.0)
    positive_reasons = _expand_reason_codes(raw.get("top_reasons", raw.get("pos")), risk=False, limit=3)
    risk_reasons = _expand_reason_codes(raw.get("risk_reasons", raw.get("risk")), risk=True, limit=3)
    confidence = _clean_confidence(raw.get("confidence", raw.get("c")))
    recommended_action = str(raw.get("recommended_action", raw.get("action")) or "").strip()
    ranking_note = str(raw.get("ranking_note", raw.get("note")) or "").strip()

    if not recommended_action:
        if risk_reasons:
            recommended_action = "Rank with caution and address the leading completion risk before outreach."
        elif probability >= 0.7:
            recommended_action = "Prioritize this match in the next invitation batch."
        elif probability >= 0.45:
            recommended_action = "Keep this match in the standard recommendation set."
        else:
            recommended_action = "Deprioritize this match unless the candidate pool is limited."
    if not ranking_note:
        ranking_note = f"Compact Claude estimate ({confidence} confidence)."

    result = {
        "survey_id": getattr(survey, "id", None),
        "participant_id": getattr(user, "id", None),
        "completion_probability": probability,
        "confidence": confidence,
        "segment_label": raw.get("segment_label") or user_segment_label(user),
        "top_reasons": positive_reasons,
        "risk_reasons": risk_reasons,
        "recommended_action": recommended_action,
        "ranking_note": ranking_note,
        "features": {
            "llm_only": True,
            "provider": "anthropic",
            "model": claude_model_name(),
            "raw_context_summary": context,
            "llm_output": {
                "recommended_action": recommended_action,
                "ranking_note": ranking_note,
            },
        },
        "model_version": MODEL_VERSION,
        "cached": False,
        "llm_ok": True,
    }
    if not result["top_reasons"]:
        result["top_reasons"] = ["Claude based the estimate on the supplied task and behavior facts."]
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
    if limit_pool is None:
        try:
            limit_pool = int(os.environ.get("AI_GROWTH_LLM_CANDIDATE_LIMIT", "60"))
        except Exception:
            limit_pool = 60
    limit_pool = max(1, min(500, limit_pool))
    users = db.query(User).filter(User.id != survey.publisher_id).order_by(User.created_at.desc()).limit(limit_pool).all()
    eligible = [user for user in users if survey_match_result(survey, user, strict=True).eligible]
    return eligible


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


def _summary_mode() -> str:
    mode = (os.environ.get("AI_GROWTH_LLM_SUMMARY_MODE") or "local").strip().lower()
    return mode if mode in {"local", "llm"} else "local"


def _local_summary_guidance(survey: Survey, summary: dict, survey_context: dict) -> tuple[str, str]:
    """Create dashboard copy without a second LLM call.

    Claude still owns every completion probability and ranking decision. This
    helper only turns already-computed aggregate facts into concise UI guidance.
    """
    duration = int(getattr(survey, "estimated_time", 0) or 0)
    reward_per_minute = survey_context.get("reward_per_minute_raw")
    destination = safe_survey_payload(survey).get("destination_type")
    low_count = int(summary.get("low_probability_count") or 0)
    candidate_count = int(summary.get("candidate_count") or 0)

    if duration >= 20:
        action = "Shorten the task or split it into smaller sections before increasing outreach."
    elif reward_per_minute is not None and float(reward_per_minute) < 0.15:
        action = "Increase the reward or reduce estimated completion time to improve follow-through."
    elif destination == "external_or_interview":
        action = "Clarify the external handoff and completion-return steps before scaling invitations."
    elif candidate_count and low_count / candidate_count >= 0.5:
        action = "Broaden non-sensitive targeting or improve the study description before scaling."
    else:
        action = "Prioritize the highest-probability candidates and monitor observed completion after launch."

    top_segments = summary.get("top_segments") or []
    if top_segments:
        labels = [str(item.get("segment_label") or "").strip() for item in top_segments[:2]]
        labels = [label for label in labels if label]
        audience = "Start with " + " and ".join(labels) + ", then expand based on observed completions."
    else:
        audience = "Start with active eligible participants, then expand after collecting completion evidence."
    return action, audience


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

    survey_context = survey_context_features(db, survey)
    local_action, local_audience = _local_summary_guidance(survey, preliminary_summary, survey_context)
    preliminary_summary["recommended_action"] = local_action
    preliminary_summary["audience_strategy"] = local_audience

    # The candidate probabilities and ranking already came from Claude. The
    # default local mode avoids paying for a second model call merely to rewrite
    # those aggregates as dashboard copy. Set AI_GROWTH_LLM_SUMMARY_MODE=llm to
    # restore model-written summaries when desired.
    if _summary_mode() != "llm":
        return preliminary_summary

    payload = {
        "task": "summarize_survey_prediction",
        "survey": safe_survey_payload(survey),
        "survey_context": survey_context,
        "aggregate": {
            "candidate_count": preliminary_summary["candidate_count"],
            "completion_probability": preliminary_summary["completion_probability"],
            "top_candidate_probability": preliminary_summary["top_candidate_probability"],
            "confidence": preliminary_summary["confidence"],
            "segment_label": preliminary_summary["segment_label"],
            "top_segments": preliminary_summary["top_segments"][:3],
            "top_reasons": preliminary_summary["top_reasons"][:3],
            "risk_reasons": preliminary_summary["risk_reasons"][:3],
            "high_probability_count": preliminary_summary["high_probability_count"],
            "medium_probability_count": preliminary_summary["medium_probability_count"],
            "low_probability_count": preliminary_summary["low_probability_count"],
        },
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
        )[:4],
    }
    llm_summary = summarize_survey_with_claude(payload)
    if llm_summary.error or not llm_summary.data:
        preliminary_summary["recommended_action"] = "Claude participant predictions were generated, but Claude summary generation failed. Review top respondent reasons directly."
        preliminary_summary["audience_strategy"] = llm_summary.error or "Claude returned no summary data."
        return preliminary_summary

    data = llm_summary.data
    preliminary_summary["completion_probability"] = _clamp_probability(data.get("completion_probability", data.get("p")), avg_prob)
    preliminary_summary["confidence"] = _clean_confidence(data.get("confidence", data.get("c")) or preliminary_summary["confidence"])
    preliminary_summary["segment_label"] = str(data.get("segment_label", data.get("segment")) or preliminary_summary["segment_label"])
    preliminary_summary["top_reasons"] = _clean_list(data.get("top_reasons", data.get("pos")), preliminary_summary.get("top_reasons", []), limit=3)
    preliminary_summary["risk_reasons"] = _clean_list(data.get("risk_reasons", data.get("risk")), preliminary_summary.get("risk_reasons", []), limit=3)
    preliminary_summary["recommended_action"] = str(data.get("recommended_action", data.get("action")) or local_action).strip()
    preliminary_summary["audience_strategy"] = str(data.get("audience_strategy", data.get("audience")) or local_audience).strip()
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

    try:
        pool_limit = max(10, min(100, int(os.environ.get("AI_GROWTH_LLM_PREVIEW_POOL_LIMIT", "60"))))
    except Exception:
        pool_limit = 60
    try:
        sample_limit = max(3, min(12, int(os.environ.get("AI_GROWTH_LLM_PREVIEW_SAMPLE_SIZE", "6"))))
    except Exception:
        sample_limit = 6
    users = db.query(User).order_by(User.created_at.desc()).limit(pool_limit).all()

    eligibility = [(user, _eligibility_context(survey, user)) for user in users]
    eligible_users = [user for user, context in eligibility if context.get("eligible")]
    representative_users = (eligible_users or users)[:sample_limit]

    def distribution(attribute: str, limit: int = 6) -> dict[str, int]:
        values = [str(getattr(user, attribute, "") or "").strip() for user in users]
        counts = Counter(value for value in values if value)
        return dict(counts.most_common(limit))

    segment_counts = Counter(user_segment_label(user) for user in users)
    participant_sample = [
        {
            "profile": safe_user_payload(user),
            "behavior": _compact_facts(user_history_features(db, user, None)),
        }
        for user in representative_users
    ]
    pool_summary = _compact_facts({
        "pool_count": len(users),
        "eligible_count": len(eligible_users),
        "sample_count": len(participant_sample),
        "top_segments": dict(segment_counts.most_common(6)),
        "age_ranges": distribution("age_range"),
        "education_levels": distribution("education_level"),
        "fields": distribution("field"),
        "statuses": distribution("status"),
        "devices": distribution("device_type"),
        "languages": distribution("language"),
    })

    llm_result = preview_survey_with_claude({
        "task": "preview_unsaved_survey",
        "survey_draft": safe_survey_payload(survey),
        "pool_summary": pool_summary,
        "representative_sample": participant_sample,
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
        "completion_probability": _clamp_probability(data.get("completion_probability", data.get("p")), 0.0),
        "confidence": _clean_confidence(data.get("confidence", data.get("c"))),
        "segment_label": str(data.get("segment_label", data.get("segment")) or "Preview audience"),
        "top_reasons": _clean_list(data.get("top_reasons", data.get("pos")), limit=3),
        "risk_reasons": _clean_list(data.get("risk_reasons", data.get("risk")), limit=3),
        "recommended_action": str(data.get("recommended_action", data.get("action")) or "").strip(),
        "audience_strategy": str(data.get("audience_strategy", data.get("audience")) or "").strip(),
        "llm_ok": True,
    }


def recommended_action(summary: dict) -> str:
    """Backward-compatible helper for old callers.

    The old implementation generated a rule-based action. This version simply
    returns the Claude-generated action if present, otherwise a neutral message.
    """
    return str(summary.get("recommended_action") or "Review the Claude-generated prediction and recommendation details.").strip()
