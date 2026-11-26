"""Features / participation / start."""

from datetime import datetime
from datetime import timezone
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.google_calendar import queue_calendar_sync
from app.models import Response
from app.models import Survey
from app.models import User
from app.settlements import lock_study
from app.trust_engine import BLOCK as TRUST_BLOCK
from app.trust_engine import public_reason_codes


async def start_survey(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.analytics.events import _event_source_from_request
    from app.features.analytics.events import _record_user_event
    from app.features.bookings.service import _deliver_or_queue_booking_confirmation
    from app.features.bookings.slots import _booking_slots_label
    from app.features.bookings.slots import _parse_booking_slots
    from app.features.bookings.slots import _serialize_booking_slots
    from app.features.jobs.followups import _enqueue_survey_start_followup_if_needed
    from app.features.screening.service import _participation_decision
    from app.features.screening.service import _screening_passed
    from app.features.screening.service import _start_gate_response
    from app.features.studies.fields import _normalize_task_type
    from app.features.studies.fields import _uses_booking_flow

    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey: raise HTTPException(404, "Survey not found")
    if survey.status != "published": raise HTTPException(400, "Survey not published")
    if not _screening_passed(db, survey_id, current_user.id):
        return JSONResponse({
            "ok": False,
            "screening_required": True,
            "screening_url": f"/surveys/{survey_id}/screening",
            "detail": "Complete the study eligibility check before choosing a time.",
        }, status_code=409)
    trust_recommendation, _ = _participation_decision(
        db,
        request,
        participant=current_user,
        survey=survey,
        stage="start",
    )
    if trust_recommendation.outcome == TRUST_BLOCK:
        db.commit()
        return _start_gate_response(trust_recommendation, survey)

    booking_slots = []
    try:
        body = await request.json()
        if isinstance(body, dict):
            if isinstance(body.get("booking_slots"), list):
                booking_slots = [str(item).strip() for item in body.get("booking_slots") if str(item).strip()]
            else:
                booking_slots = _parse_booking_slots((body.get("booking_slot") or "").strip())
    except Exception:
        booking_slots = []
    booking_slot = _serialize_booking_slots(booking_slots)

    is_live_study = _uses_booking_flow(getattr(survey, "task_type", None))
    if is_live_study:
        # Availability and its resulting booking must share one study lock.
        # Read the request body first: do not hold a DB lock across an await.
        if db.bind.dialect.name == "sqlite":
            db.execute(update(Survey).where(Survey.id == survey_id).values(id=Survey.id))
        survey = lock_study(db, survey_id)
        if not survey:
            raise HTTPException(404, "Survey not found")
        if survey.status != "published":
            raise HTTPException(400, "Survey not published")
    existing = db.query(Response).filter(
        Response.survey_id == survey_id, Response.participant_id == current_user.id
    ).first()

    should_schedule_followup = False
    should_send_booking_email = False
    response = existing
    next_response_status = (
        "scheduled" if booking_slots and is_live_study
        else "scheduling" if is_live_study
        else "started"
    )

    if booking_slots and _uses_booking_flow(getattr(survey, "task_type", None)):
        existing_bookings = db.query(Response).filter(
            Response.survey_id == survey_id,
            Response.booking_slot.isnot(None),
            Response.status.notin_(["no_show", "cancelled", "rejected"]),
            Response.participant_id != current_user.id,
        ).all()
        taken_slots = set()
        for existing_booking in existing_bookings:
            taken_slots.update(_parse_booking_slots(existing_booking.booking_slot))
        if any(slot in taken_slots for slot in booking_slots):
            raise HTTPException(409, "That time was just booked. Please choose another available time.")

    if not response:
        response = Response(
            survey_id=survey_id,
            participant_id=current_user.id,
            status=next_response_status,
            booking_slot=booking_slot,
            start_followup_scheduled_at=datetime.utcnow(),
        )
        db.add(response)
        db.commit()
        db.refresh(response)
        should_schedule_followup = True
        should_send_booking_email = bool(booking_slot)
    elif response.status != "completed":
        response.status = next_response_status
        response.completed_at = None
        response.started_at = datetime.now(timezone.utc)
        if booking_slot:
            should_send_booking_email = response.booking_slot != booking_slot
            response.booking_slot = booking_slot
        db.commit()
    elif booking_slot:
        should_send_booking_email = response.booking_slot != booking_slot
        response.booking_slot = booking_slot
        db.commit()

    if response and not response.start_followup_sent_at and not response.start_followup_scheduled_at:
        response.start_followup_scheduled_at = datetime.utcnow()
        db.commit()
        should_schedule_followup = True

    if should_schedule_followup and response:
        _enqueue_survey_start_followup_if_needed(db, response)
    if should_send_booking_email and booking_slot and response:
        queue_calendar_sync(db, response)
        _deliver_or_queue_booking_confirmation(
            db,
            survey=survey,
            participant=current_user,
            response=response,
            booking_slot=booking_slot,
        )

    _record_user_event(
        db,
        request,
        "study_start",
        user=current_user,
        target_type="survey",
        target_id=survey.id,
        page_path=f"/surveys/{survey.id}/start",
        metadata={
            "study_title": survey.title,
            "source": _event_source_from_request(request, "Study Page"),
            "booking_slot": _booking_slots_label(booking_slot) if booking_slot else "",
            "booking_slots": booking_slots,
            "task_type": _normalize_task_type(getattr(survey, "task_type", None)),
            "user_role": "participant",
        },
    )
    db.commit()

    return {"message": "Survey started successfully"}


def start_survey_and_redirect(
    survey_id: int,
    request: Request,
    next_url: str = Query("", alias="next"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.analytics.events import _event_source_from_request
    from app.features.analytics.events import _record_user_event
    from app.features.auth.navigation import _participant_dashboard_url
    from app.features.jobs.followups import _enqueue_survey_start_followup_if_needed
    from app.features.screening.service import _participation_decision
    from app.features.screening.service import _screening_passed
    from app.features.screening.service import _study_verification_url
    from app.features.studies.fields import _normalize_task_type
    from app.features.studies.fields import _uses_booking_flow

    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    if survey.status != "published":
        raise HTTPException(400, "Survey not published")
    if not _screening_passed(db, survey_id, current_user.id):
        return RedirectResponse(f"/surveys/{survey_id}/screening", status_code=303)
    trust_recommendation, _ = _participation_decision(
        db,
        request,
        participant=current_user,
        survey=survey,
        stage="start",
    )
    if trust_recommendation.outcome == TRUST_BLOCK:
        db.commit()
        codes = public_reason_codes(trust_recommendation.reasons)
        if "verification_required" in codes:
            return RedirectResponse(
                _study_verification_url(survey),
                status_code=303,
            )
        return RedirectResponse("/participation-review", status_code=303)

    target_url = (next_url or "").strip()
    if not (target_url.startswith("https://") or target_url.startswith("http://")):
        target_url = _participant_dashboard_url(request)

    should_schedule_followup = False
    is_live_study = _uses_booking_flow(getattr(survey, "task_type", None))
    redirect_status = "scheduling" if is_live_study else "started"
    response = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.participant_id == current_user.id,
    ).first()
    if not response:
        response = Response(
            survey_id=survey_id,
            participant_id=current_user.id,
            status=redirect_status,
            start_followup_scheduled_at=datetime.utcnow(),
        )
        db.add(response)
        db.commit()
        db.refresh(response)
        should_schedule_followup = True
    elif response.status != "completed":
        response.status = redirect_status
        response.completed_at = None
        response.started_at = datetime.now(timezone.utc)
        if not response.start_followup_sent_at and not response.start_followup_scheduled_at:
            response.start_followup_scheduled_at = datetime.utcnow()
            should_schedule_followup = True
        db.commit()

    if should_schedule_followup and response:
        _enqueue_survey_start_followup_if_needed(db, response)

    _record_user_event(
        db,
        request,
        "study_start",
        user=current_user,
        target_type="survey",
        target_id=survey.id,
        page_path=f"/surveys/{survey.id}/start-redirect",
        metadata={
            "study_title": survey.title,
            "source": _event_source_from_request(request, "Study Page"),
            "task_type": _normalize_task_type(getattr(survey, "task_type", None)),
            "user_role": "participant",
            "redirect_target": target_url,
        },
    )
    db.commit()

    return RedirectResponse(target_url, status_code=303)


__all__ = ['start_survey', 'start_survey_and_redirect']
