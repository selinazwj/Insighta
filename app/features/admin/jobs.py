"""Features / admin / jobs."""

from datetime import datetime
from datetime import timedelta
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Any
from app.database import get_db
from app.jobs.service import DEFAULT_STALE_RUNNING_MINUTES
from app.jobs.service import DONE
from app.jobs.service import FAILED
from app.jobs.service import PENDING
from app.jobs.service import RUNNING
from app.models import BackgroundJob


def _background_job_payload(job: BackgroundJob) -> dict[str, Any]:
    payload = job.payload_json or {}
    safe_payload = {}
    safe_keys = {
        "user_id",
        "email",
        "survey_id",
        "survey_ids",
        "response_id",
        "filename",
        "campaign_key",
        "cleanup_window",
        "auto_approve",
        "auto_approve_min_score",
    }
    for key, value in payload.items():
        if key in safe_keys or key.endswith("_id") or key.endswith("_ids"):
            safe_payload[key] = value
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "run_after": job.run_after.isoformat() if job.run_after else None,
        "locked_at": job.locked_at.isoformat() if job.locked_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "last_error": (job.last_error or "")[:500],
        "payload": safe_payload,
    }


async def admin_background_jobs(
    admin_key: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    from app.features.admin.access import _admin_key_matches

    if not _admin_key_matches(admin_key):
        raise HTTPException(403, "Unauthorized")
    now = datetime.utcnow()
    status_rows = (
        db.query(BackgroundJob.status, func.count(BackgroundJob.id))
        .group_by(BackgroundJob.status)
        .all()
    )
    counts = {status: 0 for status in [PENDING, RUNNING, DONE, FAILED]}
    counts.update({status or "unknown": count for status, count in status_rows})
    due_pending = db.query(func.count(BackgroundJob.id)).filter(
        BackgroundJob.status == PENDING,
        BackgroundJob.run_after <= now,
        BackgroundJob.attempts < BackgroundJob.max_attempts,
    ).scalar() or 0
    stale_running = db.query(func.count(BackgroundJob.id)).filter(
        BackgroundJob.status == RUNNING,
        BackgroundJob.locked_at.isnot(None),
        BackgroundJob.locked_at < now - timedelta(minutes=DEFAULT_STALE_RUNNING_MINUTES),
    ).scalar() or 0
    recent_jobs = (
        db.query(BackgroundJob)
        .order_by(BackgroundJob.updated_at.desc(), BackgroundJob.id.desc())
        .limit(limit)
        .all()
    )
    return JSONResponse({
        "counts": counts,
        "due_pending": due_pending,
        "stale_running": stale_running,
        "jobs": [_background_job_payload(job) for job in recent_jobs],
    })


async def admin_run_due_background_jobs(request: Request):
    from app.features.admin.access import _admin_key_matches
    from app.features.jobs.service import run_due_background_jobs

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")):
        raise HTTPException(403, "Unauthorized")
    try:
        limit = int(body.get("limit", 25))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid limit")
    limit = min(100, max(1, limit))
    result = run_due_background_jobs(limit=limit)
    return JSONResponse({"ok": True, **result})


__all__ = ['_background_job_payload', 'admin_background_jobs', 'admin_run_due_background_jobs']
