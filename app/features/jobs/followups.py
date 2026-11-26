"""Features / jobs / followups."""

from datetime import datetime
from datetime import timedelta
from sqlalchemy.orm import Session
from typing import Optional
import asyncio
from app.bootstrap.settings import SURVEY_START_FOLLOWUP_DELAY_MINUTES
from app.bootstrap.settings import SURVEY_START_FOLLOWUP_POLL_SECONDS
from app.jobs.service import enqueue_survey_start_followup_job
from app.models import BackgroundJob
from app.models import Response


def _survey_start_followup_run_after(response: Response) -> datetime:
    scheduled_at = response.start_followup_scheduled_at or datetime.utcnow()
    return scheduled_at + timedelta(minutes=SURVEY_START_FOLLOWUP_DELAY_MINUTES)


def _enqueue_survey_start_followup_if_needed(db: Session, response: Response) -> Optional[BackgroundJob]:
    if not response or response.start_followup_sent_at:
        return None
    if not response.start_followup_scheduled_at:
        response.start_followup_scheduled_at = datetime.utcnow()
    return enqueue_survey_start_followup_job(
        db,
        response_id=response.id,
        run_after=_survey_start_followup_run_after(response),
    )


def _enqueue_due_survey_start_followup_jobs(db: Session, *, limit: int = 25) -> int:
    due_before = datetime.utcnow() - timedelta(minutes=SURVEY_START_FOLLOWUP_DELAY_MINUTES)
    due_responses = db.query(Response).filter(
        Response.start_followup_scheduled_at.isnot(None),
        Response.start_followup_sent_at.is_(None),
        Response.start_followup_scheduled_at <= due_before,
    ).order_by(Response.start_followup_scheduled_at.asc()).limit(limit).all()
    enqueued = 0
    for response in due_responses:
        job = _enqueue_survey_start_followup_if_needed(db, response)
        if job:
            enqueued += 1
    if enqueued:
        db.commit()
    return enqueued


def _send_due_survey_start_followups(limit: int = 25) -> int:
    from app.features.jobs.service import run_due_background_jobs

    result = run_due_background_jobs(limit=limit)
    return result.get("succeeded", 0)


async def _survey_start_followup_worker() -> None:
    await asyncio.sleep(10)
    while True:
        try:
            sent_count = _send_due_survey_start_followups()
            if sent_count:
                print(f"Survey start follow-up worker sent {sent_count} email(s)")
        except Exception as exc:
            print(f"Survey start follow-up worker error: {exc}")
        await asyncio.sleep(SURVEY_START_FOLLOWUP_POLL_SECONDS)


__all__ = ['_survey_start_followup_run_after', '_enqueue_survey_start_followup_if_needed', '_enqueue_due_survey_start_followup_jobs', '_send_due_survey_start_followups', '_survey_start_followup_worker']
