"""Features / participants / routes."""

from fastapi import Depends
from fastapi import Request
from sqlalchemy.orm import Session
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.google_calendar import calendar_status
from app.models import Response
from app.models import User


def participant_my_studies(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.dashboard.presentation import _participant_survey_payload
    from app.features.dashboard.queries import _completed_counts_for_surveys
    from app.features.dashboard.queries import _surveys_by_id

    responses = db.query(Response).filter(
        Response.participant_id == current_user.id
    ).order_by(Response.started_at.desc()).all()

    survey_ids = [response.survey_id for response in responses]
    surveys_by_id = _surveys_by_id(db, survey_ids)
    completed_counts = _completed_counts_for_surveys(db, survey_ids)
    surveys_data = []
    for response in responses:
        survey = surveys_by_id.get(response.survey_id)
        if survey:
            surveys_data.append(
                _participant_survey_payload(
                    survey,
                    db,
                    current_user,
                    response,
                    completed_count=completed_counts.get(survey.id, 0),
                )
            )

    return templates.TemplateResponse("participant_my_studies.html", {
        "calendar_connection": calendar_status(db, current_user.id),
        "calendar_return": "/my-studies",
        "request": request,
        "current_user": current_user,
        "surveys": surveys_data,
        "pending_earnings": getattr(current_user, 'pending_earnings', 0.0) or 0.0,
    })


def participant_earnings(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.dashboard.presentation import _participant_survey_payload
    from app.features.dashboard.queries import _completed_counts_for_surveys
    from app.features.dashboard.queries import _surveys_by_id

    responses = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed"
    ).order_by(Response.completed_at.desc()).all()

    survey_ids = [response.survey_id for response in responses]
    surveys_by_id = _surveys_by_id(db, survey_ids)
    completed_counts = _completed_counts_for_surveys(db, survey_ids)
    surveys_data = []
    for response in responses:
        survey = surveys_by_id.get(response.survey_id)
        if survey:
            surveys_data.append(
                _participant_survey_payload(
                    survey,
                    db,
                    current_user,
                    response,
                    completed_count=completed_counts.get(survey.id, 0),
                )
            )

    return templates.TemplateResponse("participant_earnings.html", {
        "request": request,
        "current_user": current_user,
        "surveys": surveys_data,
        "pending_earnings": getattr(current_user, 'pending_earnings', 0.0) or 0.0,
        "total_withdrawn": getattr(current_user, 'total_withdrawn', 0.0) or 0.0,
        "stripe_onboarding_complete": getattr(current_user, 'stripe_onboarding_complete', 'false'),
    })


__all__ = ['participant_my_studies', 'participant_earnings']
