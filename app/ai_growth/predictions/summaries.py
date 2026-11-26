"""Publisher prediction summaries and draft previews."""
from __future__ import annotations

from typing import Optional
from sqlalchemy.orm import Session
from types import SimpleNamespace
from app.models import Survey
from app.models import User
from app.ai_growth.llm import preview_survey_with_claude
from app.ai_growth.segments import rebuild_segment_stats
from app.ai_growth.llm import safe_survey_payload
from app.ai_growth.llm import safe_user_payload
from app.ai_growth.llm import summarize_survey_with_claude
from app.ai_growth.segments import user_segment_key
from app.ai_growth.segments import user_segment_label

from .config import (
    MODEL_VERSION,
)
from .normalization import (
    _clamp_probability,
    _clean_confidence,
    _clean_list,
)
from .context import (
    _eligibility_context,
    survey_context_features,
    user_history_features,
)
from .completion import (
    candidate_users_for_survey,
    predict_users_for_survey,
)

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

def survey_prediction_summary(
    db: Session,
    survey: Survey,
    force: bool = False,
    candidate_limit: Optional[int] = None,
    allow_llm: bool = True,
) -> dict:
    candidates = candidate_users_for_survey(db, survey, limit_pool=candidate_limit)
    predictions = predict_users_for_survey(
        db,
        survey,
        candidates,
        use_cache=not force,
        allow_llm=allow_llm,
    )
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

    if not allow_llm:
        preliminary_summary["recommended_action"] = "Cached AI analysis shown. Use the explicit AI action to refresh it."
        preliminary_summary["audience_strategy"] = "No new LLM call was made."
        return preliminary_summary

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
