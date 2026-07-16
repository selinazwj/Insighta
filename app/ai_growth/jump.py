"""One-click jump gateway service.

All task entry points should pass through this service so Insighta gets a
reliable Response record, JumpEvent record, token, and funnel trail.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.models import Notification, Response, Survey, User
from app.payouts import mark_response_under_review
from app.ai_growth.matching import survey_match_result
from app.ai_growth.models import JumpEvent, UserActivityEvent
from app.ai_growth.security import (
    append_query_params,
    base_url_from_request,
    generate_plain_token,
    request_ip_hash,
    request_user_agent,
    short_hash,
    token_hash,
    validate_external_url,
)


TOKEN_TTL_DAYS = 7


def normalize_task_type(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"interview", "online_interview", "online-interview", "remote_interview"}:
        return "interview"
    if normalized in {"in_person", "in-person", "in_person_study"}:
        return "in_person"
    return "survey"


def get_or_create_response(db: Session, survey: Survey, user: User) -> Response:
    response = db.query(Response).filter(
        Response.survey_id == survey.id,
        Response.participant_id == user.id,
    ).first()
    if not response:
        response = Response(
            survey_id=survey.id,
            participant_id=user.id,
            status="started",
            start_followup_scheduled_at=datetime.utcnow(),
        )
        db.add(response)
        db.flush()
    elif response.status not in {"completed", "rejected"}:
        response.status = "started"
        if not getattr(response, "start_followup_sent_at", None) and not getattr(response, "start_followup_scheduled_at", None):
            response.start_followup_scheduled_at = datetime.utcnow()
    return response


def build_destination(db: Session, survey: Survey, request: Request, response: Response, token: str, source: str) -> tuple[str, str]:
    task_type = normalize_task_type(getattr(survey, "task_type", None))
    form_url = (getattr(survey, "form_url", None) or "").strip()

    if form_url == "__builtin__":
        return "builtin", f"/surveys/{survey.id}/take?rid={response.id}&token={token}"

    if task_type == "interview":
        safe_url = validate_external_url(form_url)
        destination_type = "interview"
    else:
        safe_url = validate_external_url(form_url)
        destination_type = "external"

    base_url = base_url_from_request(request)
    return_url = f"{base_url}/surveys/{survey.id}/return?token={token}&status=returned"
    final_url = append_query_params(safe_url, {
        "utm_source": "insighta",
        "utm_medium": source or "dashboard",
        "insighta_rid": response.id,
        "insighta_token": token,
        "return_url": return_url,
    })
    return destination_type, final_url


def create_jump_event(
    db: Session,
    survey: Survey,
    user: User,
    response: Response,
    request: Request,
    source: str,
    destination_type: str,
    destination_url: str,
    token: str,
) -> JumpEvent:
    event = JumpEvent(
        survey_id=survey.id,
        participant_id=user.id,
        response_id=response.id,
        source=source or "dashboard",
        destination_type=destination_type,
        destination_url_hash=short_hash(destination_url),
        token_hash=token_hash(token),
        token_expires_at=datetime.utcnow() + timedelta(days=TOKEN_TTL_DAYS),
        status="clicked",
        user_agent=request_user_agent(request),
        ip_hash=request_ip_hash(request),
        metadata_json={"task_type": normalize_task_type(getattr(survey, "task_type", None))},
    )
    db.add(event)
    db.add(UserActivityEvent(
        user_id=user.id,
        survey_id=survey.id,
        event_type="task_jump_clicked",
        metadata_json={"source": source, "destination_type": destination_type},
    ))
    db.commit()
    db.refresh(event)
    return event


def start_jump(db: Session, survey: Survey, user: User, request: Request, source: str = "dashboard") -> dict:
    if not survey:
        raise HTTPException(404, "Survey not found")
    if survey.status != "published":
        raise HTTPException(400, "Survey is not published")

    match = survey_match_result(survey, user, strict=True)
    if not match.eligible:
        raise HTTPException(403, f"Your profile does not match this task: {', '.join(match.failed_fields) or 'targeting rules'}")

    response = get_or_create_response(db, survey, user)
    if response.status == "completed":
        return {
            "already_completed": True,
            "response_id": response.id,
            "destination_type": "completed",
            "redirect_url": "/dashboard?task_completed=1",
            "token": None,
        }

    token = generate_plain_token()
    destination_type, redirect_url = build_destination(db, survey, request, response, token, source)
    create_jump_event(db, survey, user, response, request, source, destination_type, redirect_url, token)
    return {
        "already_completed": False,
        "response_id": response.id,
        "destination_type": destination_type,
        "redirect_url": redirect_url,
        "token": token,
    }


def find_jump_event_by_token(db: Session, token: str, survey_id: Optional[int] = None) -> JumpEvent:
    event_hash = token_hash(token)
    query = db.query(JumpEvent).filter(JumpEvent.token_hash == event_hash)
    if survey_id is not None:
        query = query.filter(JumpEvent.survey_id == survey_id)
    event = query.first()
    if not event:
        raise HTTPException(404, "Invalid jump token")
    if event.token_expires_at and event.token_expires_at < datetime.utcnow():
        event.status = "expired"
        db.commit()
        raise HTTPException(400, "Jump token has expired")
    return event


def mark_returned(db: Session, token: str, survey_id: int, status: str = "returned") -> JumpEvent:
    event = find_jump_event_by_token(db, token, survey_id)
    event.returned_at = datetime.utcnow()
    if event.status != "completed":
        event.status = "returned"
    event.metadata_json = {**(event.metadata_json or {}), "return_status": status}
    db.add(UserActivityEvent(
        user_id=event.participant_id,
        survey_id=event.survey_id,
        event_type="task_jump_returned",
        metadata_json={"status": status},
    ))
    db.commit()
    db.refresh(event)
    return event


def complete_response_with_token(db: Session, token: str, survey_id: int, request: Optional[Request] = None) -> Response:
    event = find_jump_event_by_token(db, token, survey_id)
    response = db.query(Response).filter(Response.id == event.response_id).first()
    survey = db.query(Survey).filter(Survey.id == event.survey_id).first()
    participant = db.query(User).filter(User.id == event.participant_id).first()
    if not response or not survey or not participant:
        raise HTTPException(404, "Jump response context not found")

    if response.status != "completed":
        response.status = "completed"
        response.completed_at = datetime.now(timezone.utc)
        response.payout_amount = survey.reward_amount
        mark_response_under_review(response)

        existing_notif = db.query(Notification).filter(
            Notification.survey_id == survey.id,
            Notification.participant_id == participant.id,
            Notification.status == "pending",
        ).first()
        if not existing_notif:
            db.add(Notification(
                publisher_id=survey.publisher_id,
                participant_id=participant.id,
                survey_id=survey.id,
                participant_email=participant.email,
                survey_title=survey.title,
                task_type=getattr(survey, "task_type", "survey") or "survey",
                status="pending",
            ))

    event.status = "completed"
    event.completed_at = datetime.utcnow()
    event.returned_at = event.returned_at or datetime.utcnow()
    db.add(UserActivityEvent(
        user_id=event.participant_id,
        survey_id=event.survey_id,
        event_type="task_completed_with_token",
        metadata_json={"source": event.source, "destination_type": event.destination_type},
    ))
    db.commit()
    db.refresh(response)
    return response


def mark_latest_jump_completed_for_response(db: Session, response: Response) -> None:
    event = db.query(JumpEvent).filter(JumpEvent.response_id == response.id).order_by(JumpEvent.clicked_at.desc()).first()
    if not event:
        return
    event.status = "completed"
    event.completed_at = datetime.utcnow()
    event.returned_at = event.returned_at or datetime.utcnow()
    db.add(UserActivityEvent(
        user_id=response.participant_id,
        survey_id=response.survey_id,
        event_type="task_completed_builtin",
        metadata_json={"response_id": response.id},
    ))
    db.commit()
