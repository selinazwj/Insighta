"""Features / messaging / study messages."""

from datetime import datetime
from sqlalchemy.orm import Session
import html
from app.models import Response
from app.models import Survey
from app.models import User


def _send_survey_start_followup_email(db: Session, response_id: int, dashboard_url: str) -> None:
    from app.features.messaging.delivery import send_email

    response = db.query(Response).filter(Response.id == response_id).first()
    if not response or getattr(response, "start_followup_sent_at", None):
        return

    participant = db.query(User).filter(User.id == response.participant_id).first()
    survey = db.query(Survey).filter(Survey.id == response.survey_id).first()
    if not participant or not participant.email or not survey:
        return

    first_name = (getattr(participant, "first_name", None) or getattr(participant, "username", None) or "there").strip()
    study_name = (survey.title or "the study").strip()
    escaped_first = html.escape(first_name)
    escaped_study = html.escape(study_name)
    escaped_dashboard = html.escape(dashboard_url, quote=True)
    subject = "How did the survey go?"
    text_body = f"""Hi {first_name},

We saw you started the {study_name} survey, just checking in: did you manage to finish it?

If you already submitted it, wonderful. Thank you for taking the time! If you stopped partway through, no worries at all. You can pick up right where you left off from your dashboard whenever you're ready: {dashboard_url}

If something wasn't clear, or you have any questions about the study, just reply to this email and a real person will help. No rush, and no pressure.

Warmly,
The Insighta Team"""
    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 620px; margin: 0 auto; padding: 28px 22px; color: #1a1a18; line-height: 1.65;">
      <h2 style="margin:0 0 18px;color:#168aad;">How did the survey go?</h2>
      <p>Hi {escaped_first},</p>
      <p>We saw you started the <strong>{escaped_study}</strong> survey, just checking in: did you manage to finish it?</p>
      <p>If you already submitted it, wonderful. Thank you for taking the time! If you stopped partway through, no worries at all. You can pick up right where you left off from your dashboard whenever you're ready: <a href="{escaped_dashboard}">dashboard</a>.</p>
      <p>If something wasn't clear, or you have any questions about the study, just reply to this email and a real person will help. No rush, and no pressure.</p>
      <p>Warmly,<br>The Insighta Team</p>
    </div>
    """
    sent, error = send_email(participant.email, subject, body, text_body=text_body)
    if sent:
        response.start_followup_sent_at = datetime.utcnow()
        db.commit()
    elif error:
        print(f"Survey start follow-up email error for response {response_id}: {error}")


__all__ = ['_send_survey_start_followup_email']
