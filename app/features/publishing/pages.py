"""Features / publishing / pages."""

from app import wallet as researcher_wallet
from fastapi import Depends
from fastapi import Query
from fastapi import Request
from sqlalchemy.orm import Session
from typing import Optional
import secrets
from app.bootstrap.runtime import templates
from app.bootstrap.settings import STRIPE_PUBLISHABLE_KEY
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Survey
from app.models import User


def publish_page(
    request: Request,
    builtin: int = 0,
    survey_id: Optional[int] = None,
    title: Optional[str] = None,
    desc: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing_survey = None
    if survey_id:
        existing_survey = db.query(Survey).filter(
            Survey.id == survey_id,
            Survey.publisher_id == current_user.id,
        ).first()
    is_builtin = bool(builtin) or (
        existing_survey is not None and existing_survey.form_url == "__builtin__"
    )
    return templates.TemplateResponse("publish_external.html", {
        "request": request,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "builtin": is_builtin,
        "existing_survey_id": survey_id or 0,
        "publish_token": secrets.token_urlsafe(24),
        "prefill_title": title or (existing_survey.title if existing_survey else ""),
        "prefill_desc": desc or (existing_survey.description if existing_survey else ""),
        "existing_survey": existing_survey,
        "current_user": current_user,
        "wallet": researcher_wallet.summary(db, current_user.id)
    })


def publish_interview_page(
    request: Request,
    mode: str = Query("in_person"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_online = (mode or "").strip().lower() in {"online", "remote", "video"}
    return templates.TemplateResponse("publish_interview.html", {
        "request": request,
        "current_user": current_user,
        "is_online_interview": is_online,
        "interview_mode": "online" if is_online else "in_person",
        "wallet": researcher_wallet.summary(db, current_user.id),
        "publish_token": secrets.token_urlsafe(24),
    })


__all__ = ['publish_page', 'publish_interview_page']
