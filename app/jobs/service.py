from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import BackgroundJob


PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

SURVEY_START_FOLLOWUP_EMAIL = "survey_start_followup_email"
BOOKING_CONFIRMATION_EMAIL = "booking_confirmation_email"
REWARD_SETUP_REMINDER_EMAIL = "reward_setup_reminder_email"

JobHandler = Callable[[Session, BackgroundJob], None]


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
    db.add(job)
    db.flush()
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


def mark_failed(db: Session, job: BackgroundJob, error: str) -> None:
    job.last_error = error[:2000]
    job.locked_at = None
    job.updated_at = datetime.utcnow()
    if job.attempts >= job.max_attempts:
        job.status = FAILED
    else:
        job.status = PENDING
        job.run_after = datetime.utcnow() + timedelta(minutes=min(60, 2 ** max(job.attempts - 1, 0)))
    db.commit()


def run_due_jobs(
    db: Session,
    handlers: dict[str, JobHandler],
    *,
    limit: int = 25,
) -> dict[str, int]:
    claimed = claim_due_jobs(db, limit=limit)
    processed = 0
    succeeded = 0
    failed = 0
    skipped = 0
    for job in claimed:
        processed += 1
        handler = handlers.get(job.job_type)
        if not handler:
            mark_failed(db, job, f"No handler registered for job_type={job.job_type}")
            failed += 1
            continue
        try:
            handler(db, job)
            mark_done(db, job)
            succeeded += 1
        except Exception as exc:
            mark_failed(db, job, str(exc))
            failed += 1
    return {
        "claimed": len(claimed),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
    }
