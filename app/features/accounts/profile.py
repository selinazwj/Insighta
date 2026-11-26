"""Features / accounts / profile."""

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


def profile_get(
    request: Request,
    participant_app: Optional[str] = Cookie(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.auth.navigation import _should_use_participant_app
    from app.features.auth.referrals import _ensure_user_referral_code
    from app.features.auth.referrals import _referral_invite_url

    referral_code = _ensure_user_referral_code(db, current_user, commit=True)
    invite_url = _referral_invite_url(
        referral_code,
        role="participant" if _should_use_participant_app(request, participant_app) else "researcher",
    )
    if _should_use_participant_app(request, participant_app):
        return templates.TemplateResponse("participant_profile.html", {
            "request": request,
            "current_user": current_user,
            "pending_earnings": getattr(current_user, 'pending_earnings', 0.0) or 0.0,
            "referral_code": referral_code,
            "invite_url": invite_url,
        })
    prev_url = request.headers.get("referer", "/publisher")
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": current_user,
        "prev_url": prev_url,
        "referral_code": referral_code,
        "invite_url": invite_url,
    })


def profile_edit_get(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.auth.referrals import _ensure_user_referral_code
    from app.features.auth.referrals import _referral_invite_url

    prev_url = request.headers.get("referer", "/profile")
    referral_code = _ensure_user_referral_code(db, current_user, commit=True)
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": current_user,
        "prev_url": prev_url,
        "referral_code": referral_code,
        "invite_url": _referral_invite_url(referral_code, role="researcher"),
    })


async def profile_post(
    request: Request,
    participant_app: Optional[str] = Cookie(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.analytics.events import _record_user_event
    from app.features.auth.navigation import _should_use_participant_app
    from app.features.matching.eligibility import _age_range_from_birth_date
    from app.features.matching.eligibility import _list_with_other
    from app.features.matching.eligibility import _value_with_other

    form = await request.form()
    if not _should_use_participant_app(request, participant_app):
        current_user.username = (form.get("username") or "").strip() or None
        if "first_name" in form:
            current_user.first_name = (form.get("first_name") or "").strip() or None
        if "last_name" in form:
            current_user.last_name = (form.get("last_name") or "").strip() or None
        current_user.email = (form.get("email") or "").strip()
        if "phone_number" in form:
            current_user.phone_number = (form.get("phone_number") or "").strip() or None
        current_user.field = _value_with_other(form.get("field"), form.get("field_other"))
        current_user.status = _value_with_other(form.get("status"), form.get("status_other"))
        current_user.profile_description = (form.get("profile_description") or "").strip() or None
        db.commit()
        return_to = form.get("return_to")
        if return_to and is_safe_internal_next(return_to):
            return RedirectResponse(return_to, status_code=303)
        return RedirectResponse("/publisher", status_code=303)

    language_list = _list_with_other(form.getlist("language"), form.get("language_other"))
    experience_list = _list_with_other(form.getlist("experience_tags"), form.get("experience_tags_other"))
    lifestyle_list = _list_with_other(form.getlist("lifestyle_tags"), form.get("lifestyle_tags_other"))

    current_user.username = form.get("username")
    if "first_name" in form:
        current_user.first_name = (form.get("first_name") or "").strip() or None
    if "last_name" in form:
        current_user.last_name = (form.get("last_name") or "").strip() or None
    current_user.email = form.get("email") or ""
    if "phone_number" in form:
        current_user.phone_number = (form.get("phone_number") or "").strip() or None

    birth_year = form.get("birth_year")
    birth_month = form.get("birth_month")
    derived_age_range = _age_range_from_birth_date(birth_year, birth_month)
    current_user.birth_year = birth_year or None
    current_user.birth_month = birth_month or None
    current_user.age_range = derived_age_range or form.get("age_range")
    current_user.profile_description = form.get("profile_description")

    current_user.education_level = form.get("education_level")
    current_user.field = _value_with_other(form.get("field"), form.get("field_other"))
    current_user.status = _value_with_other(form.get("status"), form.get("status_other"))

    current_user.current_country = form.get("current_country") or "United States"
    current_user.current_province = form.get("current_province")
    current_user.current_city = form.get("current_city")
    current_user.origin_country = form.get("origin_country") or ("United States" if form.get("origin_province") else None)
    current_user.origin_province = form.get("origin_province")
    current_user.origin_city = form.get("origin_city")
    current_user.state = form.get("state") or form.get("current_province")

    current_user.race = _value_with_other(form.get("race"), form.get("race_other"))
    current_user.ethnicity = form.get("ethnicity") or current_user.race
    current_user.mental_health_diagnosis = _value_with_other(
        form.get("mental_health_diagnosis"),
        form.get("mental_health_diagnosis_other"),
    )
    current_user.physical_health_diagnosis = _value_with_other(
        form.get("physical_health_diagnosis"),
        form.get("physical_health_diagnosis_other"),
    )
    current_user.sexual_orientation = form.get("sexual_orientation")
    current_user.sport_type = form.get("sport_type"); current_user.sport_frequency = form.get("sport_frequency")
    current_user.smoking = form.get("smoking"); current_user.cannabis_use = form.get("cannabis_use")
    current_user.language = ",".join(language_list) if language_list else None
    current_user.student_status = _value_with_other(form.get("student_status"), form.get("student_status_other"))
    current_user.year_in_school = _value_with_other(form.get("year_in_school"), form.get("year_in_school_other"))
    current_user.international_domestic = form.get("international_domestic")
    current_user.experience_tags = ",".join(experience_list) if experience_list else None
    current_user.income_level = form.get("income_level")
    current_user.lifestyle_tags = ",".join(lifestyle_list) if lifestyle_list else None
    current_user.participation_format = _value_with_other(form.get("participation_format"), form.get("participation_format_other"))
    current_user.device_type = _value_with_other(form.get("device_type"), form.get("device_type_other"))
    _record_user_event(
        db,
        request,
        "profile_update",
        user=current_user,
        metadata={"source": "Profile", "user_role": "participant"},
    )
    db.commit()
    return_to = form.get("return_to")
    if return_to and is_safe_internal_next(return_to):
        return RedirectResponse(return_to, status_code=303)
    if _should_use_participant_app(request, participant_app):
        return RedirectResponse("/profile", status_code=303)
    return RedirectResponse("/publisher", status_code=303)


__all__ = ['profile_get', 'profile_edit_get', 'profile_post']
