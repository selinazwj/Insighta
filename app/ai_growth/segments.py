"""Segment building and segment stats for publisher-facing AI summaries."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Response, Survey, User
from app.ai_growth.models import SurveySegmentStats


def _clean(value: Any, fallback: str = "Unknown") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def user_segment_key(user: User) -> str:
    language = _clean(getattr(user, "language", None)).split(",")[0].strip() or "Unknown"
    device = _clean(getattr(user, "device_type", None))
    participation = _clean(getattr(user, "participation_format", None))
    student = _clean(getattr(user, "student_status", None), "General")
    return f"lang={language}|device={device}|mode={participation}|student={student}"


def user_segment_label(user: User) -> str:
    parts = []
    if getattr(user, "language", None):
        parts.append(_clean(user.language).split(",")[0])
    if getattr(user, "device_type", None):
        parts.append(_clean(user.device_type))
    if getattr(user, "participation_format", None):
        parts.append(_clean(user.participation_format))
    if getattr(user, "student_status", None):
        parts.append(_clean(user.student_status))
    return " + ".join(parts[:4]) if parts else "General respondents"


def rebuild_segment_stats(db: Session, survey: Survey) -> list[dict]:
    responses = db.query(Response).filter(Response.survey_id == survey.id).all()
    grouped: dict[str, dict] = defaultdict(lambda: {
        "segment_key": "",
        "segment_label": "",
        "starts": 0,
        "completes": 0,
        "minutes": [],
    })

    for r in responses:
        user = db.query(User).filter(User.id == r.participant_id).first()
        if not user:
            continue
        key = user_segment_key(user)
        grouped[key]["segment_key"] = key
        grouped[key]["segment_label"] = user_segment_label(user)
        grouped[key]["starts"] += 1
        if r.status == "completed":
            grouped[key]["completes"] += 1
            if r.started_at and r.completed_at:
                try:
                    grouped[key]["minutes"].append(max(0, (r.completed_at - r.started_at).total_seconds() / 60.0))
                except Exception:
                    pass

    result = []
    for key, item in grouped.items():
        starts = item["starts"]
        completes = item["completes"]
        rate = completes / starts if starts else 0.0
        avg_minutes = sum(item["minutes"]) / len(item["minutes"]) if item["minutes"] else None
        row = db.query(SurveySegmentStats).filter(
            SurveySegmentStats.survey_id == survey.id,
            SurveySegmentStats.segment_key == key,
        ).first()
        if not row:
            row = SurveySegmentStats(survey_id=survey.id, segment_key=key)
            db.add(row)
        row.segment_label = item["segment_label"]
        row.starts = starts
        row.completes = completes
        row.completion_rate = rate
        row.avg_completion_minutes = avg_minutes
        row.updated_at = datetime.utcnow()
        result.append({
            "segment_key": key,
            "segment_label": item["segment_label"],
            "starts": starts,
            "completes": completes,
            "completion_rate": round(rate, 4),
            "avg_completion_minutes": round(avg_minutes, 2) if avg_minutes is not None else None,
        })
    db.commit()
    return sorted(result, key=lambda x: (x["completion_rate"], x["completes"], x["starts"]), reverse=True)
