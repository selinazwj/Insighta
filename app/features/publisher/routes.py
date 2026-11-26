"""Features / publisher / routes."""

from app import niche
from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import case
from sqlalchemy import func
from sqlalchemy.orm import Session
import json
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.google_calendar import calendar_status
from app.models import Response
from app.models import StudySettlement
from app.models import Survey
from app.models import User
from app.niche_models import NicheRequest
from app.settlements import settlement_payload


def publisher_dashboard(
    request: Request,
    user_id: str = Cookie(None),
    db: Session = Depends(get_db)
):
    from app.features.auth.cookies import _clear_participant_app
    from app.features.studies.fields import _normalize_task_type
    from app.features.studies.fields import _uses_booking_flow
    from app.features.studies.sharing import _ensure_survey_share_slug

    if not user_id:
        return RedirectResponse("/login?role=researcher", status_code=303)
    try:
        current_user = db.query(User).filter(User.id == int(user_id)).first()
    except:
        return RedirectResponse("/login?role=researcher", status_code=303)
    if not current_user:
        return RedirectResponse("/login?role=researcher", status_code=303)
    all_items = db.query(Survey).filter(Survey.publisher_id == current_user.id).all()
    slugs_changed = False
    for item in all_items:
        if not getattr(item, "share_slug", None):
            _ensure_survey_share_slug(db, item)
            slugs_changed = True
    if slugs_changed:
        db.commit()
    survey_ids = [s.id for s in all_items]

    if not survey_ids:
        completed_map = {}
    else:
        completed_map = dict(
            db.query(
                Response.survey_id,
                func.sum(case((Response.status == "completed", 1), else_=0)).label("completed_cnt"),
            )
            .filter(Response.survey_id.in_(survey_ids))
            .group_by(Response.survey_id)
            .all()
        )

    survey_items = [s for s in all_items if _normalize_task_type(getattr(s, "task_type", None)) == "survey"]
    interview_items = [s for s in all_items if _uses_booking_flow(getattr(s, "task_type", None))]

    response = templates.TemplateResponse(
        "publish.html",
        {
            "request": request,
            "surveys": survey_items,
            "interviews": interview_items,
            "completed_map": completed_map,
            "niche_requests":{row.survey_id:row for row in db.query(NicheRequest).filter(NicheRequest.survey_id.in_(survey_ids)).all()},
            "current_user": current_user
        }
    )
    _clear_participant_app(response, request)
    return response


def publisher_schedule(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.bookings.slots import _booking_slots_label
    from app.features.bookings.slots import _parse_booking_slots

    interviews = db.query(Survey).filter(
        Survey.publisher_id == current_user.id,
        Survey.task_type.in_([
            "interview", "online_interview", "remote_interview",
            "in_person", "in-person", "in_person_study",
        ]),
    ).order_by(Survey.created_at.desc()).all()
    schedule_items = []
    for survey in interviews:
        availability_slots = []
        if getattr(survey, "availability_slots", None):
            try:
                parsed_slots = json.loads(survey.availability_slots)
                if isinstance(parsed_slots, list):
                    availability_slots = parsed_slots
            except Exception:
                availability_slots = []
        rows = db.query(Response, User).join(User, User.id == Response.participant_id).filter(
            Response.survey_id == survey.id,
        ).order_by(Response.started_at.desc()).all()
        bookings = [{
            "response_id": response.id,
            "participant": user.email,
            "participant_name": user.username or user.email,
            "slot": _booking_slots_label(response.booking_slot) or "External scheduling / meeting link",
            "slots": _parse_booking_slots(response.booking_slot),
            "status": response.status,
            "started_at": response.started_at,
        } for response, user in rows]
        booked_set = set()
        for item in bookings:
            booked_set.update(item.get("slots") or [])
        schedule_items.append({
            "survey": survey,
            "availability_slots": availability_slots,
            "bookings": bookings,
            "booked_set": booked_set,
            "open_count": max(len(availability_slots) - len(booked_set), 0),
        })
    return templates.TemplateResponse("publisher_schedule.html", {
        "request": request,
        "calendar_connection": calendar_status(db, current_user.id),
        "calendar_return": "/publisher/schedule",
        "current_user": current_user,
        "schedule_items": schedule_items,
    })


def publisher_study(
    survey_id: int, request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.bookings.slots import _booking_slots_label
    from app.features.bookings.slots import _parse_booking_slots
    from app.features.studies.fields import _uses_booking_flow
    from app.features.studies.sharing import _survey_share_url

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Not found")
    if niche.request_for(db, survey.id) and survey.payment_status != 'paid':
        return RedirectResponse(f'/publisher/study/{survey.id}/quote', status_code=303)
    completed_count = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed"
    ).count()
    availability_slots = []
    if getattr(survey, "availability_slots", None):
        try:
            parsed_slots = json.loads(survey.availability_slots)
            if isinstance(parsed_slots, list):
                availability_slots = parsed_slots
        except Exception:
            availability_slots = []
    booking_rows = []
    if _uses_booking_flow(getattr(survey, "task_type", None)):
        rows = db.query(Response, User).join(User, User.id == Response.participant_id).filter(
            Response.survey_id == survey_id,
        ).order_by(Response.started_at.desc()).all()
        booking_rows = [
            {
                "response_id": response.id,
                "participant": user.email,
                "slot": _booking_slots_label(response.booking_slot) or "External scheduling / meeting link",
                "slots": _parse_booking_slots(response.booking_slot),
                "status": response.status,
                "started_at": response.started_at,
            }
            for response, user in rows
        ]
    return templates.TemplateResponse("publisher_study.html", {
        "request": request,
        "survey": survey,
        "completed_count": completed_count,
        "current_user": current_user,
        "availability_slots": availability_slots,
        "booking_rows": booking_rows,
        "settlement": settlement_payload(db.query(StudySettlement).filter_by(survey_id=survey.id).first()),
        "share_url": _survey_share_url(request, db, survey, commit=True),
    })


__all__ = ['publisher_dashboard', 'publisher_schedule', 'publisher_study']
