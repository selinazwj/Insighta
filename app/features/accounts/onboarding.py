"""Features / accounts / onboarding."""

from datetime import datetime
from fastapi import Cookie
from fastapi import Depends
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import Optional
from app.ai_growth.security import is_safe_internal_next
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import User


def complete_profile_get(
    request: Request,
    next: Optional[str] = None,
    role: Optional[str] = None,
    participant_app: Optional[str] = Cookie(None),
    current_user: User = Depends(get_current_user),
):
    from app.features.auth.navigation import _needs_identity_onboarding
    from app.features.auth.navigation import _post_auth_url
    from app.features.auth.navigation import _should_use_participant_app
    from app.features.messaging.account_messages import _welcome_email_role
    from app.web.middleware import no_store_response

    return_to = next if is_safe_internal_next(next) else ""
    if not _needs_identity_onboarding(current_user):
        return RedirectResponse(return_to or _post_auth_url(request), status_code=303)
    return no_store_response(templates.TemplateResponse("complete_profile.html", {
        "request": request,
        "current_user": current_user,
        "next": return_to,
        "role": _welcome_email_role(role, return_to, participant_app),
        "error": None,
        "participant_app": _should_use_participant_app(request, participant_app),
    }))


async def complete_profile_post(
    request: Request,
    participant_app: Optional[str] = Cookie(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event
    from app.features.auth.navigation import _post_auth_url
    from app.features.auth.navigation import _should_use_participant_app
    from app.features.matching.eligibility import _age_range_from_birth_date
    from app.features.messaging.account_messages import _send_welcome_followup_email
    from app.features.messaging.account_messages import _welcome_email_role
    from app.web.middleware import no_store_response

    form = await request.form()
    first_name = (form.get("first_name") or "").strip()
    last_name = (form.get("last_name") or "").strip()
    username = (form.get("username") or "").strip()
    birth_year = (form.get("birth_year") or "").strip()
    birth_month = (form.get("birth_month") or "").strip()
    education_level = (form.get("education_level") or "").strip()
    field = (form.get("field") or "").strip()
    status = (form.get("status") or "").strip()
    current_province = (form.get("current_province") or "").strip()
    language = (form.get("language") or "").strip()
    participation_format = (form.get("participation_format") or "").strip()
    return_to = form.get("next") if is_safe_internal_next(form.get("next")) else ""
    welcome_role = _welcome_email_role(form.get("role"), return_to, participant_app)

    required_values = [
        first_name, last_name, username, birth_year, birth_month, education_level,
        field, status, current_province, language, participation_format
    ]
    if not all(required_values):
        return no_store_response(templates.TemplateResponse("complete_profile.html", {
            "request": request,
            "current_user": current_user,
            "next": return_to,
            "role": welcome_role,
            "error": "Please fill in all required profile basics.",
            "participant_app": _should_use_participant_app(request, participant_app),
        }))
    derived_age_range = _age_range_from_birth_date(birth_year, birth_month)
    if not derived_age_range:
        return no_store_response(templates.TemplateResponse("complete_profile.html", {
            "request": request,
            "current_user": current_user,
            "next": return_to,
            "role": welcome_role,
            "error": "Please enter a valid birth year and month. Participants must be 18 or older.",
            "participant_app": _should_use_participant_app(request, participant_app),
        }))

    current_user.first_name = first_name
    current_user.last_name = last_name
    current_user.username = username
    current_user.birth_year = birth_year
    current_user.birth_month = birth_month
    current_user.age_range = derived_age_range
    current_user.education_level = education_level
    current_user.field = field
    current_user.status = status
    current_user.current_country = "United States"
    current_user.current_province = current_province
    current_user.state = current_province
    current_user.language = language
    current_user.participation_format = participation_format
    if not getattr(current_user, "welcome_email_sent_at", None):
        sent, error = _send_welcome_followup_email(current_user, welcome_role)
        if sent:
            current_user.welcome_email_sent_at = datetime.utcnow()
            current_user.welcome_email_role = welcome_role
        elif error:
            print(f"Welcome email error for user {current_user.id}: {error}")
    _record_user_event(
        db,
        request,
        "profile_update",
        user=current_user,
        metadata={"source": "Complete profile", "user_role": "participant"},
    )
    db.commit()
    return RedirectResponse(return_to or _post_auth_url(request, participant_app), status_code=303)


__all__ = ['complete_profile_get', 'complete_profile_post']
