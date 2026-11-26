"""Features / jobs / handlers."""

from pathlib import Path
from sqlalchemy.orm import Session
from app.ai_growth.llm import collect_llm_usage
from app.ai_growth.prediction import recommend_surveys_for_user
from app.ai_growth.prediction import survey_prediction_summary
from app.ai_growth.prediction import top_respondents
from app.ai_usage import update_usage
from app.bootstrap.settings import BASE_URL
from app.models import BackgroundJob
from app.models import Response
from app.models import ResponseQualityCheck
from app.models import Survey
from app.models import User
from app.quality_engine import anthropic_api_key_configured
from app.quality_engine import evaluate_builtin_response
from app.quality_engine import upsert_builtin_quality_check
from app.verification.didit_processor import DiditWebhookDuplicate
from app.verification.didit_processor import DiditWebhookIgnored
from app.verification.didit_processor import process_didit_webhook_body
from app.verification.didit_processor import reconcile_didit_session


def _handle_survey_start_followup_job(db: Session, job: BackgroundJob) -> None:
    from app.features.messaging.study_messages import _send_survey_start_followup_email

    payload = job.payload_json or {}
    response_id = int(payload.get("response_id") or 0)
    if response_id <= 0:
        raise ValueError("Missing response_id for survey start follow-up job")
    response = db.query(Response).filter(Response.id == response_id).first()
    if not response or response.start_followup_sent_at:
        return
    participant = db.query(User).filter(User.id == response.participant_id).first()
    survey = db.query(Survey).filter(Survey.id == response.survey_id).first()
    if not participant or not participant.email or not survey:
        return
    dashboard_url = f"{BASE_URL.rstrip('/')}/dashboard"
    _send_survey_start_followup_email(db, response_id, dashboard_url)
    db.refresh(response)
    if not response.start_followup_sent_at:
        raise RuntimeError(f"Survey start follow-up email was not sent for response {response_id}")


def _handle_booking_confirmation_job(db: Session, job: BackgroundJob) -> None:
    from app.features.bookings.service import _send_booking_confirmation_emails

    payload = job.payload_json or {}
    response_id = int(payload.get("response_id") or 0)
    booking_slot = str(payload.get("booking_slot") or "").strip()
    if response_id <= 0:
        raise ValueError("Missing response_id for booking confirmation job")
    if not booking_slot:
        return
    response = db.query(Response).filter(Response.id == response_id).first()
    if not response:
        return
    survey = db.query(Survey).filter(Survey.id == response.survey_id).first()
    participant = db.query(User).filter(User.id == response.participant_id).first()
    if not survey or not participant:
        return
    _send_booking_confirmation_emails(db, survey, participant, booking_slot)


def _handle_reward_setup_reminder_job(db: Session, job: BackgroundJob) -> None:
    from app.features.messaging.account_messages import _send_reward_setup_reminder_email

    payload = job.payload_json or {}
    user_id = int(payload.get("user_id") or 0)
    if user_id <= 0:
        raise ValueError("Missing user_id for reward setup reminder job")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.email:
        return
    sent, error = _send_reward_setup_reminder_email(user)
    if not sent:
        raise RuntimeError(error or f"Reward setup reminder email was not sent for user {user_id}")


def _handle_admin_bulk_email_job(db: Session, job: BackgroundJob) -> None:
    from app.features.messaging.account_messages import _send_admin_bulk_email

    payload = job.payload_json or {}
    user_id = int(payload.get("user_id") or 0)
    subject = str(payload.get("subject") or "").strip()
    message = str(payload.get("message") or "").strip()
    if user_id <= 0:
        raise ValueError("Missing user_id for admin bulk email job")
    if not subject or not message:
        raise ValueError("Missing subject or message for admin bulk email job")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.email:
        return
    sent, error = _send_admin_bulk_email(user, subject, message)
    if not sent:
        raise RuntimeError(error or f"Admin bulk email was not sent for user {user_id}")


def _handle_quality_excel_import_job(db: Session, job: BackgroundJob) -> None:
    from app.features.quality.imports import _process_quality_excel_import

    payload = job.payload_json or {}
    survey_id = int(payload.get("survey_id") or 0)
    user_id = int(payload.get("user_id") or 0)
    upload_path = str(payload.get("upload_path") or "").strip()
    filename = str(payload.get("filename") or "upload.xlsx").strip() or "upload.xlsx"
    if survey_id <= 0 or user_id <= 0 or not upload_path:
        raise ValueError("Missing survey_id, user_id, or upload_path for quality Excel import job")
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == user_id,
    ).first()
    if not survey:
        raise ValueError(f"Survey {survey_id} not found for quality Excel import job")
    path = Path(upload_path)
    if not path.exists():
        raise FileNotFoundError(f"Quality Excel upload file not found: {upload_path}")
    content = path.read_bytes()
    _process_quality_excel_import(
        db,
        survey=survey,
        filename=filename,
        content=content,
        auto_approve=bool(payload.get("auto_approve")),
        auto_approve_min_score=payload.get("auto_approve_min_score"),
        use_llm=bool(payload.get("use_llm")),
    )


def _handle_dashboard_recommendations_refresh_job(db: Session, job: BackgroundJob) -> None:
    from app.features.dashboard.queries import _dashboard_ai_auto_refresh_enabled

    payload = job.payload_json or {}

    # Old deployments may already have passive recommendation-refresh jobs in
    # the queue. Do not let those legacy jobs spend tokens after on-demand mode
    # is enabled. Explicitly marked jobs are still allowed for future/manual use.
    explicit = bool(payload.get("explicit"))
    if not explicit and not _dashboard_ai_auto_refresh_enabled():
        job.payload_json = {
            **payload,
            "skipped_reason": "Passive dashboard AI refresh is disabled; LLM is on-demand only.",
        }
        return

    user_id = int(payload.get("user_id") or 0)
    survey_ids = payload.get("survey_ids") or []
    if user_id <= 0:
        raise ValueError("Missing user_id for dashboard recommendations refresh job")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return
    normalized_ids = []
    for survey_id in survey_ids:
        try:
            normalized_id = int(survey_id)
        except (TypeError, ValueError):
            continue
        if normalized_id > 0:
            normalized_ids.append(normalized_id)
    if not normalized_ids:
        return
    surveys = db.query(Survey).filter(
        Survey.id.in_(normalized_ids),
        Survey.status == "published",
    ).all()
    if not surveys:
        return
    recommend_surveys_for_user(db, surveys, user, use_cache=True)


def _handle_ai_prediction_job(db: Session, job: BackgroundJob) -> None:
    payload = dict(job.payload_json or {})
    user_id = int(payload.get("user_id") or 0)
    survey_id = int(payload.get("survey_id") or 0)
    units = max(1, int(payload.get("units") or 20))
    mode = str(payload.get("mode") or "summary")
    force = bool(payload.get("force"))
    if user_id <= 0 or survey_id <= 0:
        raise ValueError("Missing user_id or survey_id for AI prediction job")
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == user_id,
    ).first()
    if not survey:
        raise ValueError("Survey not found for AI prediction job")
    update_usage(db, job_id=job.id, status="running")
    try:
        with collect_llm_usage() as usage:
            if mode == "respondents":
                result = {
                    "survey_id": survey.id,
                    "respondents": top_respondents(
                        db,
                        survey,
                        limit=max(1, min(20, int(payload.get("display_limit") or 8))),
                        force=force,
                        candidate_limit=units,
                    ),
                }
            else:
                result = survey_prediction_summary(
                    db,
                    survey,
                    force=force,
                    candidate_limit=units,
                )
        if mode == "respondents":
            respondents = result.get("respondents") or []
            if respondents and not any(bool(item.get("llm_ok")) for item in respondents):
                raise RuntimeError("Claude did not return candidate predictions")
        elif not bool(result.get("llm_ok")):
            raise RuntimeError(
                str(result.get("audience_strategy") or result.get("recommended_action") or "Claude prediction failed")
            )
        job.payload_json = {**payload, "result": result}
        actual_units = (
            len(result.get("respondents") or [])
            if mode == "respondents"
            else int(result.get("candidate_count") or 0)
        )
        update_usage(
            db,
            job_id=job.id,
            status="succeeded",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            actual_units=actual_units,
        )
    except Exception as exc:
        retry_status = "queued" if job.attempts < job.max_attempts else "failed"
        update_usage(db, job_id=job.id, status=retry_status, error=str(exc))
        raise


def _handle_ai_quality_review_job(db: Session, job: BackgroundJob) -> None:
    payload = dict(job.payload_json or {})
    user_id = int(payload.get("user_id") or 0)
    survey_id = int(payload.get("survey_id") or 0)
    check_ids = sorted({int(value) for value in (payload.get("check_ids") or []) if int(value) > 0})
    rows = db.query(ResponseQualityCheck).filter(
        ResponseQualityCheck.id.in_(check_ids),
        ResponseQualityCheck.survey_id == survey_id,
        ResponseQualityCheck.source_type == "builtin",
    ).all()
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == user_id,
    ).first()
    if not rows or len(rows) != len(check_ids) or not survey:
        raise ValueError("Built-in quality check not found for AI review")
    if not anthropic_api_key_configured():
        raise ValueError("AI semantic review is not configured")
    update_usage(db, job_id=job.id, status="running")
    try:
        updated_rows = []
        input_tokens = 0
        output_tokens = 0
        for row in rows:
            if not row.response_id:
                raise ValueError(f"Quality check {row.id} has no built-in response")
            result = evaluate_builtin_response(
                db,
                survey_id=survey_id,
                response_id=row.response_id,
                run_llm=True,
            )
            llm_result = result.llm_result_json or {}
            if llm_result.get("error"):
                raise RuntimeError(f"AI quality review failed: {llm_result['error']}")
            updated = upsert_builtin_quality_check(
                db,
                survey_id=survey_id,
                response_id=row.response_id,
                result=result,
            )
            llm_usage = llm_result.get("_usage") or {}
            input_tokens += int(llm_usage.get("input_tokens") or 0)
            output_tokens += int(llm_usage.get("output_tokens") or 0)
            updated_rows.append({
                "check_id": updated.id,
                "quality_score": updated.quality_score,
                "quality_label": updated.quality_label,
                "semantic_risk": updated.semantic_risk,
                "reasons": updated.reasons or [],
            })
        job.payload_json = {
            **payload,
            "result": {"checks": updated_rows},
        }
        update_usage(
            db,
            job_id=job.id,
            status="succeeded",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            actual_units=len(updated_rows),
        )
    except Exception as exc:
        retry_status = "queued" if job.attempts < job.max_attempts else "failed"
        update_usage(db, job_id=job.id, status=retry_status, error=str(exc))
        raise


def _handle_trust_data_cleanup_job(db: Session, job: BackgroundJob) -> None:
    from app.features.trust.retention import _cleanup_expired_trust_data_in_session

    result = _cleanup_expired_trust_data_in_session(db)
    job.payload_json = {
        **(job.payload_json or {}),
        "result": result,
    }


def _handle_didit_webhook_process_job(db: Session, job: BackgroundJob) -> None:
    payload = job.payload_json or {}
    body = payload.get("body")
    if not isinstance(body, dict):
        raise ValueError("Didit webhook job is missing body payload")
    try:
        result = process_didit_webhook_body(db, body)
    except DiditWebhookDuplicate:
        result = {"duplicate": True}
    except DiditWebhookIgnored as exc:
        result = {"ignored": True, "reason": exc.reason}
    job.payload_json = {**payload, "result": result}


def _handle_didit_session_reconcile_job(db: Session, job: BackgroundJob) -> None:
    payload = job.payload_json or {}
    session_id = str(payload.get("session_id") or "").strip()
    user_id = payload.get("user_id")
    if not session_id or user_id is None:
        raise ValueError("Didit reconcile job is missing session_id or user_id")
    result = reconcile_didit_session(db, session_id=session_id, user_id=int(user_id))
    job.payload_json = {**payload, "result": result}


__all__ = ['_handle_survey_start_followup_job', '_handle_booking_confirmation_job', '_handle_reward_setup_reminder_job', '_handle_admin_bulk_email_job', '_handle_quality_excel_import_job', '_handle_dashboard_recommendations_refresh_job', '_handle_ai_prediction_job', '_handle_ai_quality_review_job', '_handle_trust_data_cleanup_job', '_handle_didit_webhook_process_job', '_handle_didit_session_reconcile_job']
