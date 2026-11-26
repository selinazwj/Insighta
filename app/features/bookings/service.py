"""Features / bookings / service."""

from sqlalchemy.orm import Session
from typing import Optional
import html
from app.booking_calendar import booked_events
from app.booking_calendar import google_calendar_links
from app.bootstrap.settings import BASE_URL
from app.database import SessionLocal
from app.jobs.service import enqueue_booking_confirmation_job
from app.models import Response
from app.models import Survey
from app.models import User


def _booking_calendar_path(survey: Survey, response: Optional[Response]) -> Optional[str]:
    from app.features.bookings.slots import _parse_booking_slots
    from app.features.studies.fields import _uses_booking_flow

    if not response or not _uses_booking_flow(getattr(survey, "task_type", None)):
        return None
    events = booked_events(survey.availability_slots, _parse_booking_slots(response.booking_slot))
    return f"/bookings/{response.id}/calendar.ics" if events else None


def _booking_google_calendar_links(survey: Survey, response: Optional[Response]) -> list[dict]:
    from app.features.bookings.slots import _parse_booking_slots
    from app.features.studies.fields import _is_online_interview_survey
    from app.features.studies.fields import _survey_external_start_url
    from app.features.studies.fields import _uses_booking_flow

    if not response or not _uses_booking_flow(getattr(survey, "task_type", None)):
        return []
    events = booked_events(survey.availability_slots, _parse_booking_slots(response.booking_slot))
    location = (_survey_external_start_url(survey) if _is_online_interview_survey(survey)
                else survey.interview_location) or "The researcher will share the details before the session."
    return google_calendar_links(survey.title, events, location, f"{BASE_URL.rstrip('/')}/my-studies")


def _send_booking_confirmation_emails(db: Session, survey: Survey, participant: User, booking_slot: str) -> int:
    from app.features.bookings.slots import _booking_slots_label
    from app.features.messaging.delivery import send_email
    from app.features.studies.fields import _is_online_interview_survey
    from app.features.studies.fields import _survey_external_start_url
    from app.features.studies.fields import _uses_booking_flow

    if not booking_slot or not _uses_booking_flow(getattr(survey, "task_type", None)):
        return 0
    booking_label = _booking_slots_label(booking_slot)
    response = db.query(Response).filter(
        Response.survey_id == survey.id, Response.participant_id == participant.id
    ).first()
    calendar_path = _booking_calendar_path(survey, response)
    calendar_action = (
        f'<p><a href="{html.escape(BASE_URL.rstrip("/") + calendar_path, quote=True)}" '
        'style="color:#3b7c4f;font-weight:700;">Add to your calendar</a>'
        '<br><small>Download all booked sessions for Apple Calendar, Outlook, or Google Calendar. Sign in if prompted.</small></p>'
        if calendar_path else ""
    )
    calendar_action += "".join(
        f'<p><a href="{html.escape(link["url"], quote=True)}" style="color:#3b7c4f;font-weight:700;">'
        f'{html.escape(link["label"])}</a></p>'
        for link in _booking_google_calendar_links(survey, response)
    )
    publisher = db.query(User).filter(User.id == survey.publisher_id).first()
    study_url = f"{BASE_URL.rstrip('/')}/publisher/study/{survey.id}"
    dashboard_url = f"{BASE_URL.rstrip('/')}/dashboard"
    is_online = _is_online_interview_survey(survey)
    session_kind = "online interview" if is_online else "in-person study"
    details_label = "Meeting link" if is_online else "Location"
    details = (
        _survey_external_start_url(survey)
        if is_online
        else getattr(survey, "interview_location", None)
    ) or "The researcher will share the details before the session."
    participant_name = participant.first_name or participant.username or participant.email
    sent_count = 0
    failures = []
    if participant.email:
        sent, error = send_email(
            participant.email,
            f"[Insighta] Your time is booked for {survey.title}",
            f"""
            <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:28px 22px;color:#1a1a18;line-height:1.6;">
              <h2 style="margin:0 0 10px;font-size:22px;">Your study time is booked</h2>
              <p>Hi {html.escape(participant_name)},</p>
              <p>You booked a time for <strong>{html.escape(survey.title)}</strong>.</p>
              <div style="background:#f5f3ee;border-radius:10px;padding:16px 18px;margin:18px 0;">
                <div style="font-size:12px;color:#8a8a82;text-transform:uppercase;font-weight:700;">Time</div>
                <div style="font-size:17px;font-weight:700;margin-bottom:10px;">{html.escape(booking_label)}</div>
                <div style="font-size:12px;color:#8a8a82;text-transform:uppercase;font-weight:700;">{details_label}</div>
                <div style="font-size:15px;">{html.escape(details)}</div>
              </div>
              {calendar_action}
              <p>If something changes, reply to this email and a real person will help.</p>
              <p><a href="{dashboard_url}" style="color:#3b7c4f;font-weight:700;">Back to your dashboard</a></p>
            </div>
            """
        )
        if sent:
            sent_count += 1
        else:
            failures.append(error or "participant booking confirmation email failed")
    if publisher and publisher.email:
        sent, error = send_email(
            publisher.email,
            f"[Insighta] New {session_kind} booking: {survey.title}",
            f"""
            <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:28px 22px;color:#1a1a18;line-height:1.6;">
              <h2 style="margin:0 0 10px;font-size:22px;">New participant booking</h2>
              <p><strong>{html.escape(participant.email)}</strong> booked a time for <strong>{html.escape(survey.title)}</strong>.</p>
              <div style="background:#f5f3ee;border-radius:10px;padding:16px 18px;margin:18px 0;">
                <div style="font-size:12px;color:#8a8a82;text-transform:uppercase;font-weight:700;">Time</div>
                <div style="font-size:17px;font-weight:700;margin-bottom:10px;">{html.escape(booking_label)}</div>
                <div style="font-size:12px;color:#8a8a82;text-transform:uppercase;font-weight:700;">Participant</div>
                <div style="font-size:15px;">{html.escape(participant.email)}</div>
              </div>
              {calendar_action}
              <p><a href="{study_url}" style="color:#3b7c4f;font-weight:700;">Open schedule in Insighta</a></p>
            </div>
            """
        )
        if sent:
            sent_count += 1
        else:
            failures.append(error or "publisher booking confirmation email failed")
    if failures:
        raise RuntimeError("; ".join(failures))
    return sent_count


def _deliver_or_queue_booking_confirmation(
    db: Session,
    *,
    survey: Survey,
    participant: User,
    response: Response,
    booking_slot: str,
) -> bool:
    """Send booking mail now; persist a retry only when delivery fails."""
    try:
        _send_booking_confirmation_emails(db, survey, participant, booking_slot)
        return True
    except Exception as exc:
        print(f"Booking confirmation email deferred for response {response.id}: {exc}")
        enqueue_booking_confirmation_job(
            db,
            response_id=response.id,
            booking_slot=booking_slot,
        )
        return False


def _send_booking_confirmation_emails_for_response(response_id: int, booking_slot: str) -> None:
    db = SessionLocal()
    try:
        response = db.query(Response).filter(Response.id == response_id).first()
        if not response:
            return
        survey = db.query(Survey).filter(Survey.id == response.survey_id).first()
        participant = db.query(User).filter(User.id == response.participant_id).first()
        if survey and participant:
            _send_booking_confirmation_emails(db, survey, participant, booking_slot)
    finally:
        db.close()


__all__ = ['_booking_calendar_path', '_booking_google_calendar_links', '_send_booking_confirmation_emails', '_deliver_or_queue_booking_confirmation', '_send_booking_confirmation_emails_for_response']
