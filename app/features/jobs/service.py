"""Features / jobs / service."""

from fastapi import Request
from sqlalchemy.orm import Session
from typing import Any
import hmac
import html
import os
from app.ai_usage import update_usage
from app.bootstrap.settings import BACKGROUND_JOB_ALERT_EMAIL
from app.bootstrap.settings import BASE_URL
from app.database import SessionLocal
from app.features.jobs.handlers import _handle_admin_bulk_email_job
from app.features.jobs.handlers import _handle_ai_prediction_job
from app.features.jobs.handlers import _handle_ai_quality_review_job
from app.features.jobs.handlers import _handle_booking_confirmation_job
from app.features.jobs.handlers import _handle_dashboard_recommendations_refresh_job
from app.features.jobs.handlers import _handle_didit_session_reconcile_job
from app.features.jobs.handlers import _handle_didit_webhook_process_job
from app.features.jobs.handlers import _handle_quality_excel_import_job
from app.features.jobs.handlers import _handle_reward_setup_reminder_job
from app.features.jobs.handlers import _handle_survey_start_followup_job
from app.features.jobs.handlers import _handle_trust_data_cleanup_job
from app.google_calendar import JOB_TYPE as GOOGLE_CALENDAR_SYNC
from app.google_calendar import sync_booking
from app.jobs.service import ADMIN_BULK_EMAIL
from app.jobs.service import AI_PREDICTION
from app.jobs.service import AI_QUALITY_REVIEW
from app.jobs.service import BOOKING_CONFIRMATION_EMAIL
from app.jobs.service import DASHBOARD_RECOMMENDATIONS_REFRESH
from app.jobs.service import DIDIT_SESSION_RECONCILE
from app.jobs.service import DIDIT_WEBHOOK_PROCESS
from app.jobs.service import QUALITY_EXCEL_IMPORT
from app.jobs.service import REWARD_SETUP_REMINDER_EMAIL
from app.jobs.service import SURVEY_START_FOLLOWUP_EMAIL
from app.jobs.service import TRUST_DATA_CLEANUP
from app.jobs.service import run_due_jobs
from app.models import BackgroundJob
from app.settlements import run_settlement_cycle
from app.verification.didit_processor import enqueue_stale_didit_reconcile_jobs


BACKGROUND_JOB_HANDLERS = {
    GOOGLE_CALENDAR_SYNC: sync_booking,
    ADMIN_BULK_EMAIL: _handle_admin_bulk_email_job,
    SURVEY_START_FOLLOWUP_EMAIL: _handle_survey_start_followup_job,
    BOOKING_CONFIRMATION_EMAIL: _handle_booking_confirmation_job,
    REWARD_SETUP_REMINDER_EMAIL: _handle_reward_setup_reminder_job,
    QUALITY_EXCEL_IMPORT: _handle_quality_excel_import_job,
    DASHBOARD_RECOMMENDATIONS_REFRESH: _handle_dashboard_recommendations_refresh_job,
    AI_PREDICTION: _handle_ai_prediction_job,
    AI_QUALITY_REVIEW: _handle_ai_quality_review_job,
    TRUST_DATA_CLEANUP: _handle_trust_data_cleanup_job,
    DIDIT_WEBHOOK_PROCESS: _handle_didit_webhook_process_job,
    DIDIT_SESSION_RECONCILE: _handle_didit_session_reconcile_job,
}


def _background_job_alert_payload(job: BackgroundJob) -> dict[str, Any]:
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
    return safe_payload


def _notify_background_job_failure(
    db: Session,
    job: BackgroundJob,
    error: str,
    final_failure: bool,
) -> None:
    from app.features.messaging.delivery import send_email

    if job.job_type in {AI_PREDICTION, AI_QUALITY_REVIEW}:
        update_usage(
            db,
            job_id=job.id,
            status="failed" if final_failure else "queued",
            error=error,
        )
        db.commit()
    if not final_failure or not BACKGROUND_JOB_ALERT_EMAIL:
        return
    payload = _background_job_alert_payload(job)
    payload_lines = "\n".join(f"- {key}: {value}" for key, value in payload.items()) or "- none"
    subject = f"Insighta background job failed: {job.job_type}"
    admin_url = f"{BASE_URL.rstrip('/')}/admin#jobs"
    text_body = f"""A background job reached final failure.

Job ID: {job.id}
Type: {job.job_type}
Attempts: {job.attempts}/{job.max_attempts}
Error: {error}

Safe context:
{payload_lines}

Review jobs: {admin_url}
"""
    escaped_payload = html.escape(payload_lines)
    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 640px; margin: 0 auto; padding: 28px 22px; color: #1a1a18; line-height: 1.6;">
      <h2 style="margin:0 0 16px;color:#b91c1c;">Background job failed</h2>
      <p>A background job reached final failure and will not retry automatically.</p>
      <table style="border-collapse:collapse;width:100%;margin:18px 0;">
        <tr><td style="padding:6px 0;color:#6b7280;">Job ID</td><td style="padding:6px 0;font-weight:700;">{job.id}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Type</td><td style="padding:6px 0;font-weight:700;">{html.escape(job.job_type or '')}</td></tr>
        <tr><td style="padding:6px 0;color:#6b7280;">Attempts</td><td style="padding:6px 0;font-weight:700;">{job.attempts}/{job.max_attempts}</td></tr>
      </table>
      <div style="background:#fff5f5;border:1px solid #fecaca;border-radius:10px;padding:14px 16px;margin:16px 0;">
        <div style="font-size:12px;font-weight:700;color:#991b1b;margin-bottom:6px;">Error</div>
        <pre style="white-space:pre-wrap;font-family:Menlo,Consolas,monospace;font-size:12px;margin:0;color:#7f1d1d;">{html.escape(error[:2000])}</pre>
      </div>
      <div style="background:#fafaf8;border:1px solid #eeece6;border-radius:10px;padding:14px 16px;margin:16px 0;">
        <div style="font-size:12px;font-weight:700;color:#4a4a44;margin-bottom:6px;">Safe context</div>
        <pre style="white-space:pre-wrap;font-family:Menlo,Consolas,monospace;font-size:12px;margin:0;color:#4a4a44;">{escaped_payload}</pre>
      </div>
      <p><a href="{html.escape(admin_url, quote=True)}" style="display:inline-block;background:#3b7c4f;color:#fff;text-decoration:none;border-radius:8px;padding:10px 14px;font-weight:700;">Open admin jobs</a></p>
    </div>
    """
    sent, send_error = send_email(
        BACKGROUND_JOB_ALERT_EMAIL,
        subject,
        body,
        text_body=text_body,
    )
    if not sent:
        print(f"Background job failure alert failed for job {job.id}: {send_error}")


def run_due_background_jobs(limit: int = 25) -> dict[str, int]:
    from app.features.jobs.followups import _enqueue_due_survey_start_followup_jobs
    from app.features.verification.service import _verification_satisfies

    db = SessionLocal()
    try:
        settlement_processed = run_settlement_cycle(db, _verification_satisfies, limit=limit)
        enqueued = _enqueue_due_survey_start_followup_jobs(db, limit=limit)
        didit_reconcile_enqueued = enqueue_stale_didit_reconcile_jobs(db, limit=limit)
        if didit_reconcile_enqueued:
            db.commit()
        result = run_due_jobs(
            db,
            BACKGROUND_JOB_HANDLERS,
            limit=limit,
            on_failure=_notify_background_job_failure,
        )
        result["enqueued"] = enqueued
        result["settlements_processed"] = settlement_processed
        result["didit_reconcile_enqueued"] = didit_reconcile_enqueued
        return result
    finally:
        db.close()


def _cron_secret_matches(request: Request) -> bool:
    expected = (os.environ.get("CRON_SECRET") or "").strip()
    provided = request.headers.get("authorization", "")
    return bool(expected and provided) and hmac.compare_digest(
        provided,
        f"Bearer {expected}",
    )


__all__ = ['BACKGROUND_JOB_HANDLERS', '_background_job_alert_payload', '_notify_background_job_failure', 'run_due_background_jobs', '_cron_secret_matches']
