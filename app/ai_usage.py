"""Shared, plan-agnostic controls for explicit AI work.

Insighta does not currently have paid usage tiers. Every researcher therefore
uses the same limits, configured through environment variables. Usage is stored
in the existing UserEvent audit table so this change needs no new schema.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import BackgroundJob, UserEvent


AI_USAGE_EVENT = "ai_usage"
ACTIVE_USAGE_STATUSES = {"queued", "running", "succeeded"}
SCOPE_UNITS = {"quick": 20, "standard": 50, "full": 120}


class AiUsageLimitError(ValueError):
    pass


def _positive_int_env(name: str, default: int, *, minimum: int = 1, maximum: int = 100000) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def scope_units(scope: str | None) -> tuple[str, int]:
    normalized = (scope or "quick").strip().lower()
    if normalized not in SCOPE_UNITS:
        raise AiUsageLimitError("AI scope must be quick, standard, or full")
    platform_cap = _positive_int_env("AI_MAX_UNITS_PER_OPERATION", 120, maximum=500)
    return normalized, min(SCOPE_UNITS[normalized], platform_cap)


def cooldown_minutes() -> int:
    return _positive_int_env("AI_REQUEST_COOLDOWN_MINUTES", 10, maximum=1440)


def _day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _usage_rows(db: Session, *, since: datetime) -> list[UserEvent]:
    return db.query(UserEvent).filter(
        UserEvent.event_name == AI_USAGE_EVENT,
        UserEvent.created_at >= since,
    ).all()


def usage_snapshot(db: Session, *, user_id: int, now: datetime | None = None) -> dict[str, int]:
    rows = _usage_rows(db, since=_day_start(now))
    platform_units = 0
    user_operations = 0
    for row in rows:
        metadata = row.metadata_json or {}
        if metadata.get("status") not in ACTIVE_USAGE_STATUSES:
            continue
        billed_units = (
            metadata.get("actual_units")
            if metadata.get("status") == "succeeded" and metadata.get("actual_units") is not None
            else metadata.get("units")
        )
        platform_units += max(0, int(billed_units or 0))
        if row.user_id == user_id:
            user_operations += 1
    return {
        "user_operations": user_operations,
        "user_operation_limit": _positive_int_env("AI_DAILY_USER_OPERATIONS", 10, maximum=1000),
        "platform_units": platform_units,
        "platform_unit_limit": _positive_int_env("AI_DAILY_PLATFORM_UNITS", 500, maximum=100000),
    }


def ensure_usage_available(db: Session, *, user_id: int, units: int) -> dict[str, int]:
    snapshot = usage_snapshot(db, user_id=user_id)
    if snapshot["user_operations"] >= snapshot["user_operation_limit"]:
        raise AiUsageLimitError(
            f"Daily AI operation limit reached ({snapshot['user_operation_limit']}). Try again tomorrow."
        )
    if snapshot["platform_units"] + units > snapshot["platform_unit_limit"]:
        remaining = max(0, snapshot["platform_unit_limit"] - snapshot["platform_units"])
        raise AiUsageLimitError(
            f"Today's shared AI analysis budget is nearly used. {remaining} analysis units remain."
        )
    return snapshot


def find_recent_ai_job(
    db: Session,
    *,
    job_type: str,
    user_id: int,
    fingerprint: str,
) -> BackgroundJob | None:
    cutoff = datetime.utcnow() - timedelta(minutes=cooldown_minutes())
    jobs = db.query(BackgroundJob).filter(
        BackgroundJob.job_type == job_type,
        BackgroundJob.created_at >= cutoff,
        BackgroundJob.status.in_(["pending", "running", "done"]),
    ).order_by(BackgroundJob.created_at.desc()).all()
    for job in jobs:
        payload = job.payload_json or {}
        if int(payload.get("user_id") or 0) == user_id and payload.get("fingerprint") == fingerprint:
            return job
    return None


def reserve_usage(
    db: Session,
    *,
    user_id: int,
    job: BackgroundJob,
    feature: str,
    units: int,
    survey_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> UserEvent:
    existing = db.query(UserEvent).filter(
        UserEvent.event_name == AI_USAGE_EVENT,
        UserEvent.target_type == "background_job",
        UserEvent.target_id == str(job.id),
    ).first()
    if existing:
        return existing
    payload = {
        "status": "queued",
        "feature": feature,
        "units": max(0, int(units)),
        "survey_id": survey_id,
        "job_type": job.job_type,
        **(metadata or {}),
    }
    event = UserEvent(
        user_id=user_id,
        event_name=AI_USAGE_EVENT,
        target_type="background_job",
        target_id=str(job.id),
        metadata_json=payload,
    )
    db.add(event)
    db.flush()
    return event


def update_usage(
    db: Session,
    *,
    job_id: int,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    actual_units: int | None = None,
    error: str | None = None,
) -> None:
    event = db.query(UserEvent).filter(
        UserEvent.event_name == AI_USAGE_EVENT,
        UserEvent.target_type == "background_job",
        UserEvent.target_id == str(job_id),
    ).first()
    if not event:
        return
    metadata = dict(event.metadata_json or {})
    metadata.update({
        "status": status,
        "input_tokens": max(0, int(input_tokens or 0)),
        "output_tokens": max(0, int(output_tokens or 0)),
    })
    if actual_units is not None:
        metadata["actual_units"] = max(0, int(actual_units))
    if error:
        metadata["error"] = str(error)[:500]
    else:
        metadata.pop("error", None)
    event.metadata_json = metadata
    db.flush()


def job_public_payload(job: BackgroundJob) -> dict[str, Any]:
    payload = job.payload_json or {}
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "attempts": job.attempts,
        "result": payload.get("result"),
        "scope": payload.get("scope"),
        "units": payload.get("units"),
        "mode": payload.get("mode"),
        "error": job.last_error if job.status == "failed" else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
