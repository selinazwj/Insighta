"""Features / bookings / routes."""

from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from fastapi.responses import Response as HTTPResponse
from sqlalchemy.orm import Session
from urllib.parse import urlencode
from app.booking_calendar import booked_events
from app.booking_calendar import calendar_document
from app.bootstrap.settings import BASE_URL
from app.database import get_db
from app.models import Response
from app.models import Survey


def download_booking_calendar(
    response_id: int,
    user_id: str = Cookie(None),
    db: Session = Depends(get_db),
):
    from app.features.auth.dependencies import get_current_user
    from app.features.bookings.service import _booking_calendar_path
    from app.features.bookings.slots import _parse_booking_slots
    from app.features.studies.fields import _is_online_interview_survey
    from app.features.studies.fields import _survey_external_start_url

    path = f"/bookings/{response_id}/calendar.ics"
    if not user_id:
        return RedirectResponse(f"/login?{urlencode({'next': path})}", status_code=303)
    current_user = get_current_user(user_id=user_id, db=db)
    response = db.query(Response).filter(Response.id == response_id).first()
    survey = db.query(Survey).filter(Survey.id == response.survey_id).first() if response else None
    if not survey or current_user.id not in {response.participant_id, survey.publisher_id}:
        raise HTTPException(404, "Booking not found")
    if not _booking_calendar_path(survey, response):
        raise HTTPException(409, "Calendar dates are unavailable for this booking. Please contact the researcher.")
    events = booked_events(survey.availability_slots, _parse_booking_slots(response.booking_slot))
    location = (_survey_external_start_url(survey) if _is_online_interview_survey(survey)
                else survey.interview_location) or "The researcher will share the details before the session."
    content = calendar_document(response.id, survey.title, events, location, f"{BASE_URL.rstrip('/')}/my-studies")
    return HTTPResponse(content, media_type="text/calendar", headers={
        "Content-Disposition": f'attachment; filename="insighta-booking-{response.id}.ics"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })


__all__ = ['download_booking_calendar']
