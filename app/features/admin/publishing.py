"""Features / admin / publishing."""

from app import niche
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import secrets
from app.bootstrap.runtime import templates
from app.bootstrap.settings import STRIPE_PUBLISHABLE_KEY
from app.database import get_db
from app.models import Response
from app.models import Survey


async def admin_ai_fill(request: Request):
    from app.features.admin.access import _admin_key_matches
    from app.features.ai.authoring import _ai_fill_from_prompt

    try:
        body = await request.json()
        if not _admin_key_matches(body.get("admin_key")):
            raise HTTPException(403, "Unauthorized")
        return await _ai_fill_from_prompt(body.get("prompt", ""))
    except HTTPException:
        raise
    except Exception as e:
        import traceback; print(traceback.format_exc())
        raise HTTPException(500, str(e))


def admin_publish_page(request: Request, admin_key: str = Query(None), db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.admin.access import _get_admin_publisher_user

    if not _admin_key_matches(admin_key):
        raise HTTPException(403, "Unauthorized")
    admin_user = _get_admin_publisher_user(db)
    return templates.TemplateResponse("publish_external.html", {
        "request": request,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "builtin": False,
        "existing_survey_id": 0,
        "publish_token": secrets.token_urlsafe(24),
        "prefill_title": "",
        "prefill_desc": "",
        "existing_survey": None,
        "current_user": admin_user,
        "is_admin_publish": True,
        "admin_key": admin_key,
        "admin_created": request.query_params.get("created") == "1",
    })


async def admin_publish_survey(request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.admin.access import _get_admin_publisher_user
    from app.features.publishing.routes import publish_survey
    from app.features.studies.fields import _normalize_task_type

    form = await request.form()
    if not _admin_key_matches(form.get("admin_key")):
        raise HTTPException(403, "Unauthorized")
    admin_user = _get_admin_publisher_user(db)
    admin_payment_mode = (form.get("admin_payment_mode") or "demo").strip().lower()
    if admin_payment_mode not in {"demo", "stripe"}:
        admin_payment_mode = "demo"
    display_amount_raw = (form.get("admin_display_reward_amount") or "").strip()
    display_amount = float(display_amount_raw) if display_amount_raw else None
    return await publish_survey(
        request=request,
        title=form.get("title"),
        description=form.get("description"),
        form_url=form.get("form_url"),
        task_type=_normalize_task_type(form.get("task_type")),
        category=form.get("category"),
        estimated_time=int(form.get("estimated_time") or 0),
        per_person_gross=float(form.get("per_person_gross") or 0),
        total_budget=float(form.get("total_budget") or 0),
        target_responses=int(form.get("target_responses") or 1),
        urgency_level=form.get("urgency_level"),
        incentive_type=form.get("incentive_type"),
        target_age_range=form.get("target_age_range"),
        target_field=form.get("target_field"),
        target_status=form.get("target_status"),
        target_state=form.get("target_state"),
        target_language=form.get("target_language"),
        target_ethnicity=form.get("target_ethnicity"),
        target_sexual_orientation=form.get("target_sexual_orientation"),
        target_mental_health_diagnosis=form.get("target_mental_health_diagnosis"),
        target_physical_health_diagnosis=form.get("target_physical_health_diagnosis"),
        target_sport_type=form.get("target_sport_type"),
        target_sport_frequency=form.get("target_sport_frequency"),
        target_smoking=form.get("target_smoking"),
        target_cannabis_use=form.get("target_cannabis_use"),
        target_student_status=form.get("target_student_status"),
        target_year_in_school=form.get("target_year_in_school"),
        target_international_domestic=form.get("target_international_domestic"),
        target_participation_format=form.get("target_participation_format"),
        target_device=form.get("target_device"),
        target_income_level=form.get("target_income_level"),
        raffle_prize_type=form.get("raffle_prize_type"),
        required_verification_tier=form.get("required_verification_tier"),
        required_email_domains=form.get("required_email_domains"),
        cover_image=form.get("cover_image"),
        admin_display_reward_amount=display_amount,
        admin_publish=True,
        admin_payment_mode=admin_payment_mode,
        admin_redirect_key=form.get("admin_key"),
        current_user=admin_user,
        db=db,
    )


def admin_edit_listing_page(
    request: Request,
    survey_id: int,
    admin_key: str = Query(None),
    db: Session = Depends(get_db),
):
    from app.features.admin.access import _admin_key_matches
    from app.features.admin.access import _get_admin_publisher_user

    if not _admin_key_matches(admin_key):
        raise HTTPException(403, "Unauthorized")
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    survey.current_responses = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed",
    ).count()
    admin_user = _get_admin_publisher_user(db)
    return templates.TemplateResponse("edit_publish.html", {
        "request": request,
        "survey": survey,
        "current_user": admin_user,
        "is_admin_edit": True,
        "admin_key": admin_key,
    })


async def admin_edit_listing_post(survey_id: int, request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.quality.settings import _apply_survey_auto_filter_settings
    from app.features.studies.fields import _clean_target
    from app.features.studies.fields import _parse_optional_int

    form = await request.form()
    if not _admin_key_matches(form.get("admin_key")):
        raise HTTPException(403, "Unauthorized")

    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    niche.assert_editable(db, survey)
    current_responses = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed",
    ).count()
    additional_needed = max(0, _parse_optional_int(form.get("additional_needed")) or 0)
    incentive_clean = _clean_target(form.get("incentive_type")) or "cash"
    display_amount_raw = (form.get("admin_display_reward_amount") or "").strip()

    survey.title = (form.get("title") or survey.title or "").strip()
    survey.description = (form.get("description") or "").strip()
    survey.form_url = (form.get("form_url") or "").strip()
    survey.task_type = _clean_target(form.get("task_type")) or "survey"
    survey.category = _clean_target(form.get("category")) or survey.category
    survey.estimated_time = _parse_optional_int(form.get("estimated_time")) or survey.estimated_time
    if survey.status != "published":
        try:
            survey.reward_amount = float(form.get("reward_amount") or 0)
        except (TypeError, ValueError):
            survey.reward_amount = 0.0
    survey.target_responses = current_responses + additional_needed
    survey.urgency_level = _clean_target(form.get("urgency_level"))
    survey.incentive_type = incentive_clean
    survey.raffle_prize_type = _clean_target(form.get("raffle_prize_type")) if incentive_clean == "raffle" else None
    survey.target_age_range = _clean_target(form.get("target_age_range"))
    survey.target_education_min = _parse_optional_int(form.get("target_education_min"))
    survey.target_education_max = _parse_optional_int(form.get("target_education_max"))
    survey.target_field = _clean_target(form.get("target_field"))
    survey.target_status = _clean_target(form.get("target_status"))
    survey.target_state = _clean_target(form.get("target_state"))
    survey.target_language = _clean_target(form.get("target_language"))
    survey.target_ethnicity = _clean_target(form.get("target_ethnicity"))
    survey.target_sexual_orientation = _clean_target(form.get("target_sexual_orientation"))
    survey.target_mental_health_diagnosis = _clean_target(form.get("target_mental_health_diagnosis"))
    survey.target_physical_health_diagnosis = _clean_target(form.get("target_physical_health_diagnosis"))
    survey.target_sport_type = _clean_target(form.get("target_sport_type"))
    survey.target_sport_frequency = _clean_target(form.get("target_sport_frequency"))
    survey.target_smoking = _clean_target(form.get("target_smoking"))
    survey.target_cannabis_use = _clean_target(form.get("target_cannabis_use"))
    survey.target_student_status = _clean_target(form.get("target_student_status"))
    survey.target_year_in_school = _clean_target(form.get("target_year_in_school"))
    survey.target_international_domestic = _clean_target(form.get("target_international_domestic"))
    survey.target_experience_tags = ",".join(form.getlist("target_experience_tags")) or None
    survey.target_participation_format = _clean_target(form.get("target_participation_format"))
    survey.target_device = _clean_target(form.get("target_device"))
    survey.target_income_level = _clean_target(form.get("target_income_level"))
    survey.target_lifestyle_tags = ",".join(form.getlist("target_lifestyle_tags")) or None
    survey.target_niche_requirements = _clean_target(form.get("target_niche_requirements"))
    if display_amount_raw:
        try:
            survey.admin_display_reward_amount = max(0.0, float(display_amount_raw))
        except ValueError:
            raise HTTPException(400, "Invalid display reward amount")
    else:
        survey.admin_display_reward_amount = None
    _apply_survey_auto_filter_settings(survey, form)

    cover_image = form.get("cover_image")
    if cover_image is not None and getattr(cover_image, "filename", None):
        from app.features.publishing.uploads import save_cover_image
        survey.image_url = save_cover_image(cover_image)

    db.commit()
    return RedirectResponse("/admin#listings", status_code=303)


__all__ = ['admin_ai_fill', 'admin_publish_page', 'admin_publish_survey', 'admin_edit_listing_page', 'admin_edit_listing_post']
