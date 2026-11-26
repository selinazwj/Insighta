"""Features / publishing / editing."""

from app import niche
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import stripe
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Response
from app.models import Survey
from app.models import User


def edit_survey_get(
    request: Request,
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    current_responses = db.query(Response).filter(Response.survey_id == survey_id, Response.status == "completed").count()
    survey.current_responses = current_responses
    return templates.TemplateResponse("edit_publish.html", {
        "request": request,
        "survey": survey,
        "current_user": current_user,
    })


async def edit_survey_post(
    request: Request, survey_id: int,
    title: str = Form(...), description: str = Form(...), form_url: str = Form(...),
    task_type: str = Form("survey"), category: str = Form(...),
    estimated_time: int = Form(...), reward_amount: float = Form(...), additional_needed: int = Form(...),
    urgency_level: str = Form(None), incentive_type: str = Form(None),
    target_age_range: str = Form(None), target_field: str = Form(None),
    target_status: str = Form(None), target_state: str = Form(None),
    target_language: str = Form(None), target_ethnicity: str = Form(None),
    target_sexual_orientation: str = Form(None), target_mental_health_diagnosis: str = Form(None),
    target_physical_health_diagnosis: str = Form(None), target_sport_type: str = Form(None),
    target_sport_frequency: str = Form(None), target_smoking: str = Form(None),
    target_cannabis_use: str = Form(None), target_student_status: str = Form(None),
    target_year_in_school: str = Form(None), target_international_domestic: str = Form(None),
    target_participation_format: str = Form(None), target_device: str = Form(None),
    target_income_level: str = Form(None), raffle_prize_type: str = Form(None),
    required_verification_tier: str = Form(None),
    required_email_domains: str = Form(None),
    cover_image: UploadFile = File(None),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    from app.features.quality.settings import _apply_survey_auto_filter_settings
    from app.features.studies.fields import _clean_target
    from app.features.studies.fields import _normalize_required_verification_tier
    from app.features.studies.fields import _parse_optional_int
    from app.features.verification.service import _normalize_domains

    form = await request.form()
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))
    experience_list = form.getlist("target_experience_tags")
    target_experience_tags = ",".join(experience_list) if experience_list else None
    # ive changed this — multiple required occupations, comma separated
    occupation_list = [o for o in form.getlist("required_occupation") if o]
    required_occupation = ",".join(occupation_list) if occupation_list else None
    lifestyle_list = form.getlist("target_lifestyle_tags")
    target_lifestyle_tags = ",".join(lifestyle_list) if lifestyle_list else None

    survey = db.query(Survey).filter(Survey.id == survey_id, Survey.publisher_id == current_user.id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    niche.assert_editable(db, survey)
    if survey.pricing_snapshot:
        snapshot = survey.pricing_snapshot
        current_completed = db.query(Response).filter(Response.survey_id == survey_id, Response.status == "completed").count()
        if (current_completed + additional_needed != snapshot["target_responses"]
                or reward_amount != snapshot["reward_per_person_cents"] / 100
                or task_type != survey.task_type
                or _clean_target(incentive_type) != survey.incentive_type
                or (_clean_target(urgency_level) or "flexible") != (survey.urgency_level or "flexible")):
            raise HTTPException(400, "This funded study has a fixed quote. Create a new listing to change its budget or participant count.")
    if survey.status == "published":
        reward_amount = survey.reward_amount

    current_responses = db.query(Response).filter(Response.survey_id == survey_id, Response.status == "completed").count()

    # Update all survey fields
    survey.title = title; survey.description = description; survey.form_url = form_url
    survey.task_type = task_type; survey.category = category
    survey.estimated_time = estimated_time; survey.reward_amount = reward_amount
    survey.target_responses = current_responses + additional_needed
    survey.urgency_level = _clean_target(urgency_level); survey.incentive_type = _clean_target(incentive_type)
    survey.raffle_prize_type = _clean_target(raffle_prize_type) if _clean_target(incentive_type) == "raffle" else None
    survey.target_age_range = _clean_target(target_age_range)
    survey.target_education_min = target_education_min; survey.target_education_max = target_education_max
    survey.target_field = _clean_target(target_field); survey.target_status = _clean_target(target_status)
    survey.target_state = _clean_target(target_state); survey.target_language = _clean_target(target_language)
    survey.target_ethnicity = _clean_target(target_ethnicity)
    survey.target_sexual_orientation = _clean_target(target_sexual_orientation)
    survey.target_mental_health_diagnosis = _clean_target(target_mental_health_diagnosis)
    survey.target_physical_health_diagnosis = _clean_target(target_physical_health_diagnosis)
    survey.target_sport_type = _clean_target(target_sport_type)
    survey.target_sport_frequency = _clean_target(target_sport_frequency)
    survey.target_smoking = _clean_target(target_smoking); survey.target_cannabis_use = _clean_target(target_cannabis_use)
    survey.target_student_status = _clean_target(target_student_status)
    survey.target_year_in_school = _clean_target(target_year_in_school)
    survey.target_international_domestic = _clean_target(target_international_domestic)
    survey.target_experience_tags = target_experience_tags
    survey.target_participation_format = _clean_target(target_participation_format)
    survey.target_device = _clean_target(target_device)
    survey.target_income_level = _clean_target(target_income_level)
    survey.target_lifestyle_tags = target_lifestyle_tags
    survey.target_niche_requirements = _clean_target(form.get("target_niche_requirements"))
    survey.required_occupation = _clean_target(required_occupation)
    # Older clients may omit this control. Preserve the existing requirement
    # when an unrelated field is edited instead of silently weakening it.
    if required_verification_tier is not None:
        survey.required_verification_tier = _normalize_required_verification_tier(
            required_verification_tier
        )
    survey.required_email_domains = ",".join(_normalize_domains(required_email_domains)) or None
    _apply_survey_auto_filter_settings(survey, form)

    if cover_image and cover_image.filename:
        from app.features.publishing.uploads import save_cover_image
        survey.image_url = save_cover_image(cover_image)

    db.commit()

    # Published survey with additional responses that require payment
    is_no_pay = _clean_target(incentive_type) in ("raffle", "volunteer")
    if survey.status == "published" and additional_needed > 0 and not is_no_pay and not survey.pricing_snapshot:
        total = round(reward_amount * additional_needed, 2)
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Additional responses: {survey.title}",
                        "description": f"{additional_needed} more responses × ${reward_amount:.2f} per person"
                    },
                    "unit_amount": int(round(total * 100))
                },
                "quantity": 1
            }],
            mode="payment",
            success_url=f"https://insightaco.org/payment/success?survey_id={survey.id}",
            cancel_url=f"https://insightaco.org/publisher",
            metadata={"survey_id": str(survey.id), "publisher_id": str(current_user.id)}
        )
        return RedirectResponse(session.url, status_code=303)

    return RedirectResponse("/publisher", status_code=303)


__all__ = ['edit_survey_get', 'edit_survey_post']
