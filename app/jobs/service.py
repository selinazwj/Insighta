from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BackgroundJob


PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

SURVEY_START_FOLLOWUP_EMAIL = "survey_start_followup_email"
BOOKING_CONFIRMATION_EMAIL = "booking_confirmation_email"
REWARD_SETUP_REMINDER_EMAIL = "reward_setup_reminder_email"
ADMIN_BULK_EMAIL = "admin_bulk_email"
QUALITY_EXCEL_IMPORT = "quality_excel_import"
DASHBOARD_RECOMMENDATIONS_REFRESH = "dashboard_recommendations_refresh"
AI_PREDICTION = "ai_prediction"
AI_QUALITY_REVIEW = "ai_quality_review"
TRUST_DATA_CLEANUP = "trust_data_cleanup"

JobHandler = Callable[[Session, BackgroundJob], None]
JobFailureCallback = Callable[[Session, BackgroundJob, str, bool], None]
DEFAULT_STALE_RUNNING_MINUTES = 15


def _ai_dedupe_bucket() -> int:
    try:
        minutes = max(1, min(1440, int(os.environ.get("AI_REQUEST_COOLDOWN_MINUTES", "10"))))
    except (TypeError, ValueError):
        minutes = 10
    return int(datetime.utcnow().timestamp() // (60 * minutes))


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 3,
) -> BackgroundJob:
    if dedupe_key:
        existing = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == dedupe_key).first()
        if existing:
            return existing
    job = BackgroundJob(
        job_type=job_type,
        status=PENDING,
        payload_json=payload or {},
        dedupe_key=dedupe_key,
        max_attempts=max(1, max_attempts),
        run_after=run_after or datetime.utcnow(),
    )
    if not dedupe_key:
        db.add(job)
        db.flush()
        return job
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = db.query(BackgroundJob).filter(BackgroundJob.dedupe_key == dedupe_key).first()
        if existing:
            return existing
        raise
    return job


def enqueue_survey_start_followup_job(
    db: Session,
    *,
    response_id: int,
    run_after: datetime,
) -> BackgroundJob:
    return enqueue_job(
        db,
        job_type=SURVEY_START_FOLLOWUP_EMAIL,
        payload={"response_id": response_id},
        run_after=run_after,
        dedupe_key=f"{SURVEY_START_FOLLOWUP_EMAIL}:response:{response_id}",
        max_attempts=3,
    )


def enqueue_booking_confirmation_job(
    db: Session,
    *,
    response_id: int,
    booking_slot: str,
    run_after: datetime | None = None,
) -> BackgroundJob:
    slot_key = hashlib.sha256(booking_slot.encode("utf-8")).hexdigest()[:16]
    return enqueue_job(
        db,
        job_type=BOOKING_CONFIRMATION_EMAIL,
        payload={"response_id": response_id, "booking_slot": booking_slot},
        run_after=run_after,
        dedupe_key=f"{BOOKING_CONFIRMATION_EMAIL}:response:{response_id}:slot:{slot_key}",
        max_attempts=3,
    )


def enqueue_reward_setup_reminder_job(
    db: Session,
    *,
    user_id: int,
    email: str,
    campaign_key: str,
    run_after: datetime | None = None,
) -> BackgroundJob:
    email_key = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
    return enqueue_job(
        db,
        job_type=REWARD_SETUP_REMINDER_EMAIL,
        payload={"user_id": user_id, "email": email, "campaign_key": campaign_key},
        run_after=run_after,
        dedupe_key=f"{REWARD_SETUP_REMINDER_EMAIL}:{campaign_key}:user:{user_id}:email:{email_key}",
        max_attempts=3,
    )


def enqueue_admin_bulk_email_job(
    db: Session,
    *,
    user_id: int,
    email: str,
    subject: str,
    message: str,
    campaign_key: str,
    run_after: datetime | None = None,
) -> BackgroundJob:
    email_key = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
    subject_key = hashlib.sha256(subject.strip().encode("utf-8")).hexdigest()[:12]
    return enqueue_job(
        db,
        job_type=ADMIN_BULK_EMAIL,
        payload={
            "user_id": user_id,
            "email": email,
            "subject": subject,
            "message": message,
            "campaign_key": campaign_key,
        },
        run_after=run_after,
        dedupe_key=f"{ADMIN_BULK_EMAIL}:{campaign_key}:user:{user_id}:email:{email_key}:subject:{subject_key}",
        max_attempts=3,
    )


def enqueue_quality_excel_import_job(
    db: Session,
    *,
    survey_id: int,
    user_id: int,
    upload_path: str,
    filename: str,
    auto_approve: bool,
    auto_approve_min_score: float | None = None,
    use_llm: bool = False,
    run_after: datetime | None = None,
) -> BackgroundJob:
    upload_key = hashlib.sha256(upload_path.encode("utf-8")).hexdigest()[:16]
    return enqueue_job(
        db,
        job_type=QUALITY_EXCEL_IMPORT,
        payload={
            "survey_id": survey_id,
            "user_id": user_id,
            "upload_path": upload_path,
            "filename": filename,
            "auto_approve": auto_approve,
            "auto_approve_min_score": auto_approve_min_score,
            "use_llm": bool(use_llm),
        },
        run_after=run_after,
        dedupe_key=f"{QUALITY_EXCEL_IMPORT}:survey:{survey_id}:upload:{upload_key}",
        max_attempts=2,
    )


def enqueue_dashboard_recommendations_refresh_job(
    db: Session,
    *,
    user_id: int,
    survey_ids: list[int],
    run_after: datetime | None = None,
    explicit: bool = False,
) -> BackgroundJob:
    normalized_ids = sorted({int(survey_id) for survey_id in survey_ids if int(survey_id) > 0})
    ids_key = hashlib.sha256(",".join(str(survey_id) for survey_id in normalized_ids).encode("utf-8")).hexdigest()[:16]
    refresh_window = (run_after or datetime.utcnow()).strftime("%Y%m%d%H")
    trigger_mode = "explicit" if explicit else "passive"
    return enqueue_job(
        db,
        job_type=DASHBOARD_RECOMMENDATIONS_REFRESH,
        payload={"user_id": user_id, "survey_ids": normalized_ids, "explicit": bool(explicit)},
        run_after=run_after,
        dedupe_key=f"{DASHBOARD_RECOMMENDATIONS_REFRESH}:{trigger_mode}:{refresh_window}:user:{user_id}:surveys:{ids_key}",
        max_attempts=2,
    )


def enqueue_ai_prediction_job(
    db: Session,
    *,
    user_id: int,
    survey_id: int,
    mode: str,
    scope: str,
    units: int,
    force: bool,
    fingerprint: str,
) -> BackgroundJob:
    bucket = _ai_dedupe_bucket()
    return enqueue_job(
        db,
        job_type=AI_PREDICTION,
        payload={
            "user_id": user_id,
            "survey_id": survey_id,
            "mode": mode,
            "scope": scope,
            "units": units,
            "force": bool(force),
            "fingerprint": fingerprint,
        },
        dedupe_key=f"{AI_PREDICTION}:{fingerprint}:{bucket}",
        max_attempts=2,
    )


def enqueue_ai_quality_review_job(
    db: Session,
    *,
    user_id: int,
    survey_id: int,
    check_ids: list[int],
    fingerprint: str,
) -> BackgroundJob:
    bucket = _ai_dedupe_bucket()
    return enqueue_job(
        db,
        job_type=AI_QUALITY_REVIEW,
        payload={
            "user_id": user_id,
            "survey_id": survey_id,
            "check_ids": sorted({int(check_id) for check_id in check_ids if int(check_id) > 0}),
            "scope": "single" if len(check_ids) == 1 else "flagged",
            "units": len(check_ids),
            "fingerprint": fingerprint,
        },
        dedupe_key=f"{AI_QUALITY_REVIEW}:{fingerprint}:{bucket}",
        max_attempts=2,
    )


def enqueue_trust_data_cleanup_job(
    db: Session,
    *,
    run_after: datetime | None = None,
) -> BackgroundJob:
    cleanup_window = (run_after or datetime.utcnow()).strftime("%Y%m%d")
    return enqueue_job(
        db,
        job_type=TRUST_DATA_CLEANUP,
        payload={"cleanup_window": cleanup_window},
        run_after=run_after,
        dedupe_key=f"{TRUST_DATA_CLEANUP}:{cleanup_window}",
        max_attempts=2,
    )


def recover_stale_running_jobs(
    db: Session,
    *,
    stale_after_minutes: int = DEFAULT_STALE_RUNNING_MINUTES,
    now: datetime | None = None,
    on_failure: JobFailureCallback | None = None,
) -> dict[str, int]:
    now = now or datetime.utcnow()
    cutoff = now - timedelta(minutes=max(1, stale_after_minutes))
    jobs = db.query(BackgroundJob).filter(
        BackgroundJob.status == RUNNING,
        BackgroundJob.locked_at.isnot(None),
        BackgroundJob.locked_at < cutoff,
    ).order_by(BackgroundJob.locked_at.asc(), BackgroundJob.id.asc()).all()
    recovered = 0
    failed = 0
    final_failures: list[tuple[BackgroundJob, str]] = []
    for job in jobs:
        locked_at = job.locked_at
        error = f"Recovered stale running job locked at {locked_at or 'unknown'}"
        job.locked_at = None
        job.updated_at = now
        job.last_error = error
        if job.attempts >= job.max_attempts:
            job.status = FAILED
            failed += 1
            final_failures.append((job, error))
        else:
            job.status = PENDING
            job.run_after = now
            recovered += 1
    if jobs:
        db.commit()
    if on_failure:
        for job, error in final_failures:
            on_failure(db, job, error, True)
    return {"stale_recovered": recovered, "stale_failed": failed}


def claim_due_jobs(db: Session, *, limit: int = 25, now: datetime | None = None) -> list[BackgroundJob]:
    now = now or datetime.utcnow()
    jobs = db.query(BackgroundJob).filter(
        BackgroundJob.status == PENDING,
        BackgroundJob.run_after <= now,
        BackgroundJob.attempts < BackgroundJob.max_attempts,
    ).order_by(BackgroundJob.run_after.asc(), BackgroundJob.id.asc()).limit(limit).all()
    for job in jobs:
        job.status = RUNNING
        job.locked_at = now
        job.attempts = (job.attempts or 0) + 1
        job.updated_at = now
    if jobs:
        db.commit()
    return jobs


def mark_done(db: Session, job: BackgroundJob) -> None:
    job.status = DONE
    job.locked_at = None
    job.last_error = None
    job.updated_at = datetime.utcnow()
    db.commit()


def mark_failed(db: Session, job: BackgroundJob, error: str) -> bool:
    job.last_error = error[:2000]
    job.locked_at = None
    job.updated_at = datetime.utcnow()
    final_failure = job.attempts >= job.max_attempts
    if final_failure:
        job.status = FAILED
    else:
        job.status = PENDING
        job.run_after = datetime.utcnow() + timedelta(minutes=min(60, 2 ** max(job.attempts - 1, 0)))
    db.commit()
    return final_failure


def run_due_jobs(
    db: Session,
    handlers: dict[str, JobHandler],
    *,
    limit: int = 25,
    on_failure: JobFailureCallback | None = None,
) -> dict[str, int]:
    stale_result = recover_stale_running_jobs(db, on_failure=on_failure)
    claimed = claim_due_jobs(db, limit=limit)
    processed = 0
    succeeded = 0
    failed = 0
    skipped = 0
    for job in claimed:
        processed += 1
        handler = handlers.get(job.job_type)
        if not handler:
            error = f"No handler registered for job_type={job.job_type}"
            final_failure = mark_failed(db, job, error)
            if on_failure:
                on_failure(db, job, error, final_failure)
            failed += 1
            continue
        try:
            handler(db, job)
            mark_done(db, job)
            succeeded += 1
        except Exception as exc:
            error = str(exc)
            final_failure = mark_failed(db, job, error)
            if on_failure:
                on_failure(db, job, error, final_failure)
            failed += 1
    return {
        "claimed": len(claimed),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        **stale_result,
    }
