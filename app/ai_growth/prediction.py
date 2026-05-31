"""Rule-based V0 completion prediction.

This is intentionally explainable and dependency-free. It gives Insighta a
working AI-style publisher forecast while enough JumpEvent/Response data is
being accumulated for a later supervised model.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Question, Response, Survey, User
from app.ai_growth.matching import survey_match_result
from app.ai_growth.models import JumpEvent, RespondentPrediction, UserActivityEvent
from app.ai_growth.segments import user_segment_key, user_segment_label, rebuild_segment_stats

MODEL_VERSION = "rule-v0.1"


def _clamp(value: float, lo: float = 0.02, hi: float = 0.98) -> float:
    return max(lo, min(hi, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _reward_per_minute(survey: Survey) -> float:
    minutes = max(1, int(getattr(survey, "estimated_time", 1) or 1))
    return _safe_float(getattr(survey, "reward_amount", 0.0), 0.0) / minutes


def user_history_features(db: Session, user: User, survey: Optional[Survey] = None) -> dict:
    started = db.query(Response).filter(Response.participant_id == user.id).count()
    completed = db.query(Response).filter(Response.participant_id == user.id, Response.status == "completed").count()
    smoothed_rate = (completed + 2.0) / (started + 4.0)

    rejected = db.query(Response).filter(Response.participant_id == user.id, Response.status == "rejected").count()
    rejected_rate = rejected / max(1, started)

    recent_completed = db.query(Response).filter(
        Response.participant_id == user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= datetime.utcnow() - timedelta(days=30),
    ).count()

    category_completed = 0
    category_started = 0
    if survey is not None and getattr(survey, "category", None):
        joined = db.query(Response).join(Survey, Survey.id == Response.survey_id).filter(
            Response.participant_id == user.id,
            Survey.category == survey.category,
        )
        category_started = joined.count()
        category_completed = joined.filter(Response.status == "completed").count()
    category_rate = (category_completed + 1.0) / (category_started + 2.0)

    avg_minutes = None
    completed_responses = db.query(Response).filter(
        Response.participant_id == user.id,
        Response.status == "completed",
        Response.started_at.isnot(None),
        Response.completed_at.isnot(None),
    ).limit(50).all()
    mins = []
    for r in completed_responses:
        try:
            mins.append(max(0, (r.completed_at - r.started_at).total_seconds() / 60.0))
        except Exception:
            pass
    if mins:
        avg_minutes = sum(mins) / len(mins)

    return {
        "started": started,
        "completed": completed,
        "completion_rate_smoothed": smoothed_rate,
        "rejected_rate": rejected_rate,
        "recent_completed_30d": recent_completed,
        "category_started": category_started,
        "category_completed": category_completed,
        "category_completion_rate_smoothed": category_rate,
        "avg_completion_minutes": avg_minutes,
    }


def platform_reward_score(db: Session, survey: Survey) -> float:
    rpm = _reward_per_minute(survey)
    same_category = db.query(Survey).filter(Survey.category == getattr(survey, "category", None)).all()
    rpms = [_reward_per_minute(s) for s in same_category if getattr(s, "estimated_time", None)]
    if len(rpms) < 5:
        # Cold-start thresholds: $0.05/min is weak, $0.50/min is strong.
        return _clamp((rpm - 0.05) / 0.45, 0.15, 1.0)
    below = sum(1 for x in rpms if x <= rpm)
    return _clamp(below / len(rpms), 0.1, 1.0)


def device_fit_score(survey: Survey, user: User) -> float:
    target = (getattr(survey, "target_device", None) or "").strip().lower()
    device = (getattr(user, "device_type", None) or "").strip().lower()
    if not target or target in {"all", "any", "both"}:
        return 0.8
    if not device:
        return 0.5
    if device in {"all", "any", "both"}:
        return 0.8
    return 1.0 if target == device else 0.0


def recent_activity_score(history: dict) -> float:
    if history["recent_completed_30d"] >= 3:
        return 1.0
    if history["recent_completed_30d"] == 2:
        return 0.85
    if history["recent_completed_30d"] == 1:
        return 0.7
    if history["completed"] > 0:
        return 0.55
    return 0.4


def destination_experience_score(survey: Survey) -> float:
    form_url = (getattr(survey, "form_url", None) or "").strip()
    if form_url == "__builtin__":
        return 0.95
    if getattr(survey, "task_type", None) == "interview":
        return 0.7
    return 0.55


def question_complexity_score(db: Session, survey: Survey) -> float:
    if (getattr(survey, "form_url", None) or "") != "__builtin__":
        return 0.75
    count = db.query(Question).filter(Question.survey_id == survey.id).count()
    if count <= 8:
        return 0.95
    if count <= 16:
        return 0.75
    if count <= 25:
        return 0.55
    return 0.35


def explain_reasons(survey: Survey, user: User, match_score: float, history: dict, reward_score: float, device_score: float, complexity_score: float) -> list[str]:
    reasons = []
    if match_score >= 0.85:
        reasons.append("Strong profile match")
    elif match_score >= 0.65:
        reasons.append("Acceptable profile match")
    if history["completed"] >= 3 and history["completion_rate_smoothed"] >= 0.65:
        reasons.append("Historically high completion behavior")
    elif history["completed"] > 0:
        reasons.append("Has completed tasks before")
    if reward_score >= 0.7:
        reasons.append("Reward per minute is attractive")
    if int(getattr(survey, "estimated_time", 0) or 0) <= 10:
        reasons.append("Short estimated duration")
    if device_score >= 0.9:
        reasons.append("Device requirement matches participant")
    if complexity_score >= 0.85:
        reasons.append("Low built-in question complexity")
    if not reasons:
        reasons.append("Cold-start baseline with available profile signals")
    return reasons[:5]


def explain_risks(survey: Survey, user: User, history: dict, reward_score: float, destination_score: float, complexity_score: float) -> list[str]:
    risks = []
    if destination_score < 0.7:
        risks.append("External form may cause drop-off without return tracking")
    if int(getattr(survey, "estimated_time", 0) or 0) >= 25:
        risks.append("Estimated duration is relatively long")
    if reward_score < 0.35:
        risks.append("Reward per minute may be low")
    if history["started"] < 3:
        risks.append("Limited participant history; prediction confidence is lower")
    if history["rejected_rate"] >= 0.25:
        risks.append("Prior rejection rate is elevated")
    if complexity_score < 0.55:
        risks.append("Built-in survey has many questions")
    return risks[:5]


def recommended_action(summary: dict) -> str:
    risks = summary.get("risk_reasons") or []
    avg_prob = summary.get("completion_probability", 0)
    if any("Reward" in r or "reward" in r for r in risks):
        return "Increase reward per minute or reduce estimated duration."
    if any("External" in r for r in risks):
        return "Use a tracked return URL and remind users to confirm completion."
    if any("duration" in r for r in risks):
        return "Shorten the task or split it into a smaller survey."
    if avg_prob < 0.45:
        return "Broaden target filters or improve the listing description."
    return "Prioritize the top predicted respondents and keep current settings."


def predict_user_for_survey(db: Session, survey: Survey, user: User, use_cache: bool = True) -> dict:
    if use_cache:
        cached = db.query(RespondentPrediction).filter(
            RespondentPrediction.survey_id == survey.id,
            RespondentPrediction.participant_id == user.id,
            RespondentPrediction.model_version == MODEL_VERSION,
            RespondentPrediction.expires_at > datetime.utcnow(),
        ).first()
        if cached:
            return {
                "survey_id": survey.id,
                "participant_id": user.id,
                "completion_probability": round(cached.probability, 4),
                "confidence": cached.confidence,
                "segment_label": cached.segment_label,
                "top_reasons": cached.reasons_json or [],
                "risk_reasons": cached.risk_json or [],
                "model_version": cached.model_version,
                "cached": True,
            }

    match = survey_match_result(survey, user, strict=False)
    history = user_history_features(db, user, survey)
    reward_score = platform_reward_score(db, survey)
    category_score = history["category_completion_rate_smoothed"]
    device_score = device_fit_score(survey, user)
    activity_score = recent_activity_score(history)
    destination_score = destination_experience_score(survey)
    complexity_score = question_complexity_score(db, survey)

    raw_score = (
        0.28 * match.score
        + 0.20 * history["completion_rate_smoothed"]
        + 0.14 * reward_score
        + 0.11 * category_score
        + 0.09 * device_score
        + 0.08 * activity_score
        + 0.06 * destination_score
        + 0.04 * complexity_score
    )

    if not match.eligible:
        raw_score *= 0.35
    if history["rejected_rate"] > 0:
        raw_score *= max(0.65, 1.0 - history["rejected_rate"] * 0.6)

    probability = round(_clamp(raw_score), 4)
    confidence = "low"
    if history["started"] >= 15:
        confidence = "high"
    elif history["started"] >= 5 or db.query(Response).count() >= 100:
        confidence = "medium"

    reasons = explain_reasons(survey, user, match.score, history, reward_score, device_score, complexity_score)
    risks = explain_risks(survey, user, history, reward_score, destination_score, complexity_score)
    segment_label = user_segment_label(user)
    features = {
        "profile_match": match.score,
        "eligible": match.eligible,
        "user_completion_rate": round(history["completion_rate_smoothed"], 4),
        "reward_time_score": round(reward_score, 4),
        "category_affinity": round(category_score, 4),
        "device_fit": round(device_score, 4),
        "recent_activity": round(activity_score, 4),
        "destination_experience": round(destination_score, 4),
        "question_complexity": round(complexity_score, 4),
    }

    existing = db.query(RespondentPrediction).filter(
        RespondentPrediction.survey_id == survey.id,
        RespondentPrediction.participant_id == user.id,
        RespondentPrediction.model_version == MODEL_VERSION,
    ).first()
    if not existing:
        existing = RespondentPrediction(survey_id=survey.id, participant_id=user.id, model_version=MODEL_VERSION)
        db.add(existing)
    existing.probability = probability
    existing.confidence = confidence
    existing.segment_label = segment_label
    existing.reasons_json = reasons
    existing.risk_json = risks
    existing.features_json = features
    existing.created_at = datetime.utcnow()
    existing.expires_at = datetime.utcnow() + timedelta(hours=6)
    db.commit()

    return {
        "survey_id": survey.id,
        "participant_id": user.id,
        "completion_probability": probability,
        "confidence": confidence,
        "segment_label": segment_label,
        "top_reasons": reasons,
        "risk_reasons": risks,
        "recommended_action": recommended_action({"completion_probability": probability, "risk_reasons": risks}),
        "features": features,
        "model_version": MODEL_VERSION,
        "cached": False,
    }


def candidate_users_for_survey(db: Session, survey: Survey, limit_pool: int = 500) -> list[User]:
    users = db.query(User).filter(User.id != survey.publisher_id).order_by(User.created_at.desc()).limit(limit_pool).all()
    eligible = []
    fallback = []
    for u in users:
        m = survey_match_result(survey, u, strict=True)
        if m.eligible:
            eligible.append(u)
        else:
            fallback.append(u)
    return eligible or fallback


def top_respondents(db: Session, survey: Survey, limit: int = 20, force: bool = False) -> list[dict]:
    users = candidate_users_for_survey(db, survey)
    scored = [predict_user_for_survey(db, survey, u, use_cache=not force) for u in users]
    scored.sort(key=lambda x: x["completion_probability"], reverse=True)
    return scored[:limit]


def survey_prediction_summary(db: Session, survey: Survey, force: bool = False) -> dict:
    candidates = candidate_users_for_survey(db, survey)
    predictions = [predict_user_for_survey(db, survey, u, use_cache=not force) for u in candidates]
    if not predictions:
        summary = {
            "survey_id": survey.id,
            "model_version": MODEL_VERSION,
            "candidate_count": 0,
            "completion_probability": 0.25,
            "confidence": "low",
            "segment_label": "No candidate data yet",
            "top_reasons": ["No matching respondent profiles are available yet"],
            "risk_reasons": ["Cold-start candidate pool"],
        }
        summary["recommended_action"] = recommended_action(summary)
        return summary

    probs = [p["completion_probability"] for p in predictions]
    avg_prob = sum(probs) / len(probs)
    top_probs = sorted(probs, reverse=True)[: max(1, min(10, len(probs)))]
    top_avg = sum(top_probs) / len(top_probs)
    confidence = "high" if len(predictions) >= 30 else "medium" if len(predictions) >= 10 else "low"

    reason_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    segment_scores: dict[str, list[float]] = {}
    segment_labels: dict[str, str] = {}
    for p in predictions:
        user = db.query(User).filter(User.id == p["participant_id"]).first()
        if user:
            key = user_segment_key(user)
            segment_scores.setdefault(key, []).append(p["completion_probability"])
            segment_labels[key] = user_segment_label(user)
        for r in p.get("top_reasons", []):
            reason_counts[r] = reason_counts.get(r, 0) + 1
        for r in p.get("risk_reasons", []):
            risk_counts[r] = risk_counts.get(r, 0) + 1

    segment_summary = []
    for key, scores in segment_scores.items():
        segment_summary.append({
            "segment_key": key,
            "segment_label": segment_labels.get(key, key),
            "candidate_count": len(scores),
            "avg_probability": round(sum(scores) / len(scores), 4),
        })
    segment_summary.sort(key=lambda x: (x["avg_probability"], x["candidate_count"]), reverse=True)

    funnel_segments = rebuild_segment_stats(db, survey)
    summary = {
        "survey_id": survey.id,
        "model_version": MODEL_VERSION,
        "candidate_count": len(predictions),
        "completion_probability": round(avg_prob, 4),
        "top_candidate_probability": round(top_avg, 4),
        "confidence": confidence,
        "segment_label": segment_summary[0]["segment_label"] if segment_summary else "General respondents",
        "top_segments": segment_summary[:5],
        "funnel_segments": funnel_segments[:5],
        "top_reasons": [k for k, _ in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:5]],
        "risk_reasons": [k for k, _ in sorted(risk_counts.items(), key=lambda x: x[1], reverse=True)[:5]],
        "high_probability_count": sum(1 for p in probs if p >= 0.7),
        "medium_probability_count": sum(1 for p in probs if 0.45 <= p < 0.7),
        "low_probability_count": sum(1 for p in probs if p < 0.45),
    }
    summary["recommended_action"] = recommended_action(summary)
    return summary


def preview_summary_from_payload(db: Session, payload: dict) -> dict:
    # Preview uses the same rule path with an unsaved Survey-like object.
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

    # We cannot cache preview predictions because the survey is unsaved.
    users = db.query(User).order_by(User.created_at.desc()).limit(300).all()
    probs = []
    risks = []
    reasons = []
    for u in users:
        match = survey_match_result(survey, u, strict=False)
        history = user_history_features(db, u, None)
        reward_score = platform_reward_score(db, survey)
        device_score = device_fit_score(survey, u)
        activity_score = recent_activity_score(history)
        destination_score = destination_experience_score(survey)
        score = _clamp(0.32 * match.score + 0.22 * history["completion_rate_smoothed"] + 0.18 * reward_score + 0.12 * device_score + 0.10 * activity_score + 0.06 * destination_score)
        probs.append(score)
        if int(survey.estimated_time or 0) >= 25:
            risks.append("Estimated duration may reduce completion")
        if reward_score < 0.35:
            risks.append("Reward per minute may be low")
        if destination_score < 0.7:
            risks.append("External task may add jump-off risk")
        if match.score >= 0.8:
            reasons.append("Target profile is clear")
    avg = sum(probs) / len(probs) if probs else 0.25
    summary = {
        "survey_id": None,
        "model_version": MODEL_VERSION,
        "candidate_count": len(users),
        "completion_probability": round(avg, 4),
        "confidence": "medium" if len(users) >= 30 else "low",
        "segment_label": "Preview audience",
        "top_reasons": list(dict.fromkeys(reasons))[:4] or ["Preview based on current respondent pool"],
        "risk_reasons": list(dict.fromkeys(risks))[:4],
    }
    summary["recommended_action"] = recommended_action(summary)
    return summary
