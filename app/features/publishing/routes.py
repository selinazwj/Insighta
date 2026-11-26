"""Features / publishing / routes."""

from app import niche
from app import wallet as researcher_wallet
from datetime import date
from datetime import datetime
from datetime import time
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Optional
from urllib.parse import urlencode
import asyncio
import hashlib
import json
import re
import stripe
from app.bootstrap.runtime import logger
from app.bootstrap.settings import BASE_URL
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Survey
from app.models import User
from app.pricing import PricingError
from app.pricing import calculate_quote


async def calculate_price(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Send a valid quote request.")
    if not isinstance(body, dict):
        raise HTTPException(400, "Send a quote object.")
    try:
        quote = calculate_quote(body.get("study_type", "survey"), body.get("incentive_type", "cash"),
                                body.get("per_person", "0"), body.get("target_responses", 1),
                                body.get("urgency_level", "flexible"))
    except PricingError as exc:
        raise HTTPException(400, str(exc))
    if body.get("payment_method") == "wallet":
        quote["wallet"] = researcher_wallet.payment_quote(db, current_user.id, quote, body.get("use_promo") is True)
    return JSONResponse(quote)


async def publish_interview(
    request: Request, title: str = Form(...), description: str = Form(...),
    category: str = Form(...), estimated_time: int = Form(...), target_responses: int = Form(...),
    interview_format: str = Form("video"), scheduling_link: Optional[str] = Form(None),
    availability_notes: Optional[str] = Form(None), interview_location: Optional[str] = Form(None),
    urgency_level: Optional[str] = Form(None), deadline_date: Optional[str] = Form(None),
    incentive_type: Optional[str] = Form(None), per_person_gross: Optional[str] = Form(None),
    required_verification_tier: str = Form(None),
    required_email_domains: str = Form(None),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    form = await request.form()
    online = (interview_format or "").strip().lower() in {"online", "video", "remote"}
    return await publish_survey(
        request=request, title=title, description=description, form_url=scheduling_link or "",
        task_type="online_interview" if online else "in_person", category=category,
        estimated_time=estimated_time, target_responses=target_responses,
        per_person_gross=per_person_gross, total_budget=None,
        incentive_type=incentive_type, urgency_level=urgency_level,
        required_verification_tier=required_verification_tier, required_email_domains=required_email_domains,
        **{name: form.get(name) for name in (
            "target_age_range", "target_field", "target_status", "target_state", "target_language",
            "target_ethnicity", "target_sexual_orientation", "target_mental_health_diagnosis",
            "target_physical_health_diagnosis", "target_sport_type", "target_sport_frequency",
            "target_smoking", "target_cannabis_use", "target_student_status", "target_year_in_school",
            "target_international_domestic", "target_participation_format", "target_device",
            "target_income_level", "raffle_prize_type")},
        cover_image=None, admin_display_reward_amount=None, current_user=current_user, db=db,
    )


def _publish_redirect(request: Request, url: str, survey_id: int):
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"redirect_url": url, "survey_id": survey_id})
    return RedirectResponse(url, status_code=303)


async def publish_survey(
    request: Request,
    title: str = Form(...), description: str = Form(...), form_url: str = Form(...),
    task_type: str = Form("survey"), category: str = Form(...), estimated_time: int = Form(...),
    per_person_gross: Optional[str] = Form(None), total_budget: Optional[str] = Form(None),
    target_responses: int = Form(...), urgency_level: str = Form(None), incentive_type: str = Form(None),
    target_age_range: str = Form(None), target_field: str = Form(None), target_status: str = Form(None),
    target_state: str = Form(None), target_language: str = Form(None), target_ethnicity: str = Form(None),
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
    admin_display_reward_amount: Optional[float] = Form(None),
    admin_publish: bool = False,
    admin_payment_mode: str = "demo",
    admin_redirect_key: Optional[str] = None,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    from app.features.admin.access import _admin_key_matches
    from app.features.analytics.events import _record_user_event
    from app.features.quality.settings import _apply_survey_auto_filter_settings
    from app.features.studies.fields import _clean_target
    from app.features.studies.fields import _join_form_list_with_other
    from app.features.studies.fields import _normalize_required_verification_tier
    from app.features.studies.fields import _parse_optional_int
    from app.features.studies.sharing import _ensure_survey_share_slug
    from app.features.verification.service import _normalize_domains

    form = await request.form()
    niche_requested = not admin_publish and str(form.get('niche_recruitment') or '').lower() in {'1', 'true', 'on'}
    niche_details = str(form.get('niche_details') or '').strip()
    if niche_requested and not 10 <= len(niche_details) <= 4000:
        raise HTTPException(400, 'Describe your niche audience in 10–4,000 characters.')
    if admin_publish and not _admin_key_matches(form.get("admin_key")):
        raise HTTPException(403, "Admin demo publishing requires administrator authorization.")
    admin_payment_mode = (admin_payment_mode or form.get("admin_payment_mode") or "demo").strip().lower()
    if admin_payment_mode not in {"demo", "stripe"}:
        admin_payment_mode = "demo"
    admin_uses_stripe = bool(admin_publish and admin_payment_mode == "stripe")
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))
    experience_list = form.getlist("target_experience_tags")
    target_experience_tags = ",".join(experience_list) if experience_list else None
    lifestyle_list = form.getlist("target_lifestyle_tags")
    target_lifestyle_tags = ",".join(lifestyle_list) if lifestyle_list else None
    target_niche_requirements = _clean_target(form.get("target_niche_requirements"))
    # ive changed this — researchers can now require MULTIPLE occupations (checkboxes),
    # stored comma separated like the other multi-select fields
    occupation_list = [o for o in form.getlist("required_occupation") if o]
    required_occupation = ",".join(occupation_list) if occupation_list else None
    participant_benefits = _join_form_list_with_other(
        form.getlist("participant_benefits"),
        form.get("participant_benefits_other")
    )
    try:
        existing_survey_id = int(form.get("existing_survey_id") or 0)
        if not 0 <= existing_survey_id <= 2**63 - 1:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, "Choose a valid draft or reload the publishing form.")
    raw_publish_token = (form.get("publish_token") or "").strip()
    if raw_publish_token and not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", raw_publish_token):
        raise HTTPException(400, "Invalid submission token. Reload the form and try again.")
    publish_token = f"{current_user.id}:{raw_publish_token}" if raw_publish_token else None
    if publish_token:
        previous = db.query(Survey).filter(Survey.publish_token == publish_token).first()
        if previous:
            if existing_survey_id and previous.id != existing_survey_id:
                raise HTTPException(409, "This submission belongs to another draft. Reload the form.")
            existing_survey_id = previous.id
    if existing_survey_id:
        previous = db.query(Survey).filter(
            Survey.id == existing_survey_id, Survey.publisher_id == current_user.id
        ).populate_existing().with_for_update().first()
        if not previous:
            raise HTTPException(404, "Survey not found")
        if niche.request_for(db, previous.id):
            return _publish_redirect(request, f'/publisher/study/{previous.id}/quote', previous.id)
        if previous.status in {"published", "closed"} or previous.payment_status in {"paid", "admin_demo"}:
            if admin_publish:
                redirect_qs = urlencode({"admin_key": admin_redirect_key or "", "created": "1"})
                return _publish_redirect(request, f"/admin/publish?{redirect_qs}", previous.id)
            return _publish_redirect(request, "/publisher", previous.id)
    if niche_requested and not publish_token:
        raise HTTPException(400, 'Reload the form before submitting a niche request.')
    if not title.strip() or target_responses <= 0 or estimated_time <= 0:
        raise HTTPException(400, "Add a title, a positive duration, and at least one participant.")
    raw_task_type = (task_type or form.get("task_type") or "survey").strip()
    is_online_interview = raw_task_type in {"online_interview", "remote_interview"}
    is_in_person_study = raw_task_type in {"in_person_study", "in_person"}
    is_interview_listing = raw_task_type == "interview" or is_online_interview or is_in_person_study
    normalized_task_type = "in_person" if is_in_person_study else "interview" if is_interview_listing else raw_task_type
    if is_online_interview:
        target_participation_format = "Video interview"
    elif is_in_person_study:
        target_participation_format = "In-person study"
    session_count = max(_parse_optional_int(form.get("session_count")) or 1, 1)
    sessions_per_week = _parse_optional_int(form.get("sessions_per_week"))
    availability_slots = (form.get("availability_slots") or "").strip()
    if availability_slots:
        try:
            parsed_slots = json.loads(availability_slots)
            availability_slots = json.dumps(parsed_slots if isinstance(parsed_slots, list) else [])
        except Exception:
            availability_slots = None
    else:
        availability_slots = None
    scheduling_link = (form.get("scheduling_link") or "").strip()
    interview_location = _clean_target(form.get("interview_location"))
    if is_interview_listing:
        form_url = scheduling_link or form_url or ""

    is_builtin = (form_url == "__builtin__")
    incentive_clean = _clean_target(incentive_type) or "cash"
    raffle_clean = _clean_target(raffle_prize_type)
    is_raffle = incentive_clean == "raffle"
    demo = admin_publish and not admin_uses_stripe
    quote = None
    if niche_requested:
        from app.pricing import money_cents, positive_count
        try:
            desired = money_cents(per_person_gross or '0')
            calculate_quote(normalized_task_type, incentive_clean, str(desired/100) if desired else '0.01', target_responses, urgency_level)
            positive_count(target_responses)
        except PricingError as exc:
            raise HTTPException(400, str(exc))
        ppg = reward = desired/100 if incentive_clean in {'cash', 'gift_card'} else 0
        rate = total = 0
    elif demo and str(per_person_gross or "").strip() in {"", "0", "0.0", "0.00"}:
        ppg = rate = reward = total = 0.0
    else:
        try:
            quote = calculate_quote(normalized_task_type, incentive_clean, per_person_gross or "0",
                                    target_responses, urgency_level)
        except PricingError as exc:
            raise HTTPException(400, str(exc))
        ppg = reward = quote["reward_per_person_cents"] / 100
        rate = quote["rate_bps"] / 10000
        total = quote["total_cents"] / 100

    deadline_at = None
    raw_deadline = str(form.get("deadline_date") or "").strip()
    if raw_deadline:
        try:
            deadline_at = datetime.combine(date.fromisoformat(raw_deadline), time.max)
        except ValueError:
            raise HTTPException(400, "Enter a valid deadline date (UTC).")
        if deadline_at <= datetime.utcnow():
            raise HTTPException(400, "The completion deadline must be in the future.")

    image_url = None
    if cover_image and cover_image.filename:
        from app.features.publishing.uploads import save_cover_image
        image_url = save_cover_image(cover_image)

    if existing_survey_id:
        survey = db.query(Survey).filter(
            Survey.id == existing_survey_id,
            Survey.publisher_id == current_user.id
        ).first()
        if not survey:
            raise HTTPException(404, "Survey not found")
        survey.title = title
        survey.description = description
        survey.form_url = form_url
        survey.task_type = normalized_task_type
        survey.category = category
        survey.estimated_time = estimated_time
        survey.reward_amount = reward
        survey.per_person_gross = ppg
        survey.total_budget = total
        survey.commission_rate = rate
        survey.deadline_at = deadline_at
        survey.pricing_snapshot = quote
        survey.payment_status = "unpaid"
        survey.target_responses = target_responses
        survey.urgency_level = _clean_target(urgency_level)
        survey.incentive_type = incentive_clean
        survey.raffle_prize_type = raffle_clean if is_raffle else None
        survey.target_age_range = _clean_target(target_age_range)
        survey.target_education_min = target_education_min
        survey.target_education_max = target_education_max
        survey.target_field = _clean_target(target_field)
        survey.target_status = _clean_target(target_status)
        survey.target_state = _clean_target(target_state)
        survey.target_language = _clean_target(target_language)
        survey.target_ethnicity = _clean_target(target_ethnicity)
        survey.target_sexual_orientation = _clean_target(target_sexual_orientation)
        survey.target_mental_health_diagnosis = _clean_target(target_mental_health_diagnosis)
        survey.target_physical_health_diagnosis = _clean_target(target_physical_health_diagnosis)
        survey.target_sport_type = _clean_target(target_sport_type)
        survey.target_sport_frequency = _clean_target(target_sport_frequency)
        survey.target_smoking = _clean_target(target_smoking)
        survey.target_cannabis_use = _clean_target(target_cannabis_use)
        survey.target_student_status = _clean_target(target_student_status)
        survey.target_year_in_school = None
        survey.target_international_domestic = _clean_target(target_international_domestic)
        survey.target_experience_tags = target_experience_tags
        survey.target_participation_format = _clean_target(target_participation_format)
        survey.target_device = _clean_target(target_device)
        survey.target_income_level = _clean_target(target_income_level)
        survey.target_lifestyle_tags = target_lifestyle_tags
        survey.target_niche_requirements = target_niche_requirements
        survey.required_occupation = _clean_target(required_occupation)
        if required_verification_tier is not None:
            survey.required_verification_tier = _normalize_required_verification_tier(
                required_verification_tier
            )
        survey.required_email_domains = ",".join(_normalize_domains(required_email_domains)) or None
        survey.participant_benefits = participant_benefits
        survey.availability_slots = availability_slots if is_interview_listing else None
        survey.interview_location = interview_location if is_interview_listing else None
        survey.session_count = session_count if is_interview_listing else None
        survey.sessions_per_week = sessions_per_week if is_interview_listing else None
        survey.admin_display_reward_amount = admin_display_reward_amount if admin_publish and not admin_uses_stripe else None
        if image_url:
            survey.image_url = image_url
        if publish_token and not survey.publish_token:
            survey.publish_token = publish_token
        _ensure_survey_share_slug(db, survey)
        _apply_survey_auto_filter_settings(survey, form)
        if niche_requested:
            niche.submit_request(db, survey, niche_details)
        db.commit()
        db.refresh(survey)
    else:
        survey = Survey(
            publisher_id=current_user.id, title=title, description=description, form_url=form_url,
            task_type=normalized_task_type, category=category, estimated_time=estimated_time,
            reward_amount=reward, per_person_gross=ppg, total_budget=total, commission_rate=rate,
            pricing_snapshot=quote, deadline_at=deadline_at,
            payment_status="unpaid",
            target_responses=target_responses, urgency_level=_clean_target(urgency_level),
            incentive_type=incentive_clean, raffle_prize_type=raffle_clean if is_raffle else None,
            target_age_range=_clean_target(target_age_range),
            target_education_min=target_education_min, target_education_max=target_education_max,
            target_field=_clean_target(target_field), target_status=_clean_target(target_status),
            target_state=_clean_target(target_state), target_language=_clean_target(target_language),
            target_ethnicity=_clean_target(target_ethnicity),
            target_sexual_orientation=_clean_target(target_sexual_orientation),
            target_mental_health_diagnosis=_clean_target(target_mental_health_diagnosis),
            target_physical_health_diagnosis=_clean_target(target_physical_health_diagnosis),
            target_sport_type=_clean_target(target_sport_type),
            target_sport_frequency=_clean_target(target_sport_frequency),
            target_smoking=_clean_target(target_smoking), target_cannabis_use=_clean_target(target_cannabis_use),
            target_student_status=_clean_target(target_student_status),
            target_year_in_school=None,
            target_international_domestic=_clean_target(target_international_domestic),
            target_experience_tags=target_experience_tags,
            target_participation_format=_clean_target(target_participation_format),
            target_device=_clean_target(target_device),
            target_income_level=_clean_target(target_income_level),
            target_lifestyle_tags=target_lifestyle_tags,
            target_niche_requirements=target_niche_requirements,
            required_occupation=_clean_target(required_occupation),
            required_verification_tier=_normalize_required_verification_tier(required_verification_tier),
            required_email_domains=",".join(_normalize_domains(required_email_domains)) or None,
            participant_benefits=participant_benefits,
            availability_slots=availability_slots if is_interview_listing else None,
            interview_location=interview_location if is_interview_listing else None,
            session_count=session_count if is_interview_listing else None,
            sessions_per_week=sessions_per_week if is_interview_listing else None,
            admin_display_reward_amount=admin_display_reward_amount if admin_publish and not admin_uses_stripe else None,
            image_url=image_url, status="draft", published_at=None, closed_at=None,
            publish_token=publish_token,
        )
        _ensure_survey_share_slug(db, survey)
        _apply_survey_auto_filter_settings(survey, form)
        db.add(survey)
        try:
            db.flush()
            if niche_requested:
                niche.submit_request(db, survey, niche_details)
            db.commit()
        except IntegrityError:
            db.rollback()
            previous = db.query(Survey).filter(Survey.publish_token == publish_token).first() if publish_token else None
            if not previous:
                raise
            return JSONResponse({
                "detail": "This listing is already being submitted. Please try again in a moment.",
                "survey_id": previous.id,
            }, status_code=409)
        db.refresh(survey)
        _record_user_event(
            db,
            request,
            "study_created",
            user=current_user,
            target_type="survey",
            target_id=survey.id,
            metadata={"study_title": survey.title, "source": "Publish flow", "user_role": "researcher"},
        )

    if niche_requested:
        return _publish_redirect(request, f'/publisher/study/{survey.id}/quote', survey.id)

    if admin_publish and not admin_uses_stripe:
        survey.status = "published"
        survey.payment_status = "admin_demo"
        survey.published_at = datetime.utcnow()
        _record_user_event(
            db,
            request,
            "study_published",
            user=current_user,
            target_type="survey",
            target_id=survey.id,
            metadata={"study_title": survey.title, "source": "Admin publish", "user_role": "researcher"},
        )
        db.commit()
        redirect_qs = urlencode({"admin_key": admin_redirect_key or "", "created": "1"})
        return _publish_redirect(request, f"/admin/publish?{redirect_qs}", survey.id)

    payment_method = str(form.get("payment_method") or "stripe").strip()
    destination = str(form.get("refund_destination") or "wallet").strip()
    if payment_method not in {"stripe", "wallet"} or destination not in {"wallet", "card"}:
        raise HTTPException(400, "Choose a valid payment method and refund destination.")
    survey.refund_destination = destination
    db.flush()
    if payment_method == "wallet" and not admin_uses_stripe:
        researcher_wallet.pay_study(db, survey.id, current_user.id,
            use_promo=str(form.get("use_promo") or "").lower() in {"1", "true", "on"}, expected_snapshot=quote)
        _record_user_event(db, request, "study_published", user=current_user,
            target_type="survey", target_id=survey.id,
            metadata={"study_title":survey.title, "source":"Wallet publish", "user_role":"researcher"})
        db.commit()
        return _publish_redirect(request, "/publisher", survey.id)
    survey.funding_source = "stripe"

    if not stripe.api_key:
        db.commit()
        raise HTTPException(503, "Stripe is not configured. Your listing is saved as a draft.")

    success_url = f"{BASE_URL.rstrip('/')}/payment/success?survey_id={survey.id}"
    cancel_url = f"{BASE_URL.rstrip('/')}/publisher"
    if admin_uses_stripe:
        cancel_url = f"{BASE_URL.rstrip('/')}/admin#publish"
    stripe_description = (f"{survey.target_responses} participants; full reward ${reward:.2f} each; "
                          f"recruitment ${quote['recruitment_fee_per_person_cents'] / 100:.2f} each; "
                          f"urgency ${quote['urgency_amount_cents'] / 100:.2f}")
    checkout_params = dict(
        payment_method_types=["card"],
        line_items=[{"price_data": {"currency": "usd", "product_data": {"name": f"Survey: {survey.title}", "description": stripe_description}, "unit_amount": quote["total_cents"]}, "quantity": 1}],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "survey_id": str(survey.id),
            "publisher_id": str(current_user.id),
            "source": "admin_publish_stripe" if admin_uses_stripe else "researcher_publish",
        }
    )
    checkout_fingerprint = hashlib.sha256(json.dumps(checkout_params, sort_keys=True).encode()).hexdigest()
    try:
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            **checkout_params,
            idempotency_key=f"publish:{survey.id}:{checkout_fingerprint}",
        )
    except stripe.error.StripeError:
        logger.exception("Could not start checkout for survey %s", survey.id)
        return JSONResponse({
            "detail": "Payment checkout could not be started. Your listing is saved as a draft. Please try again.",
            "survey_id": survey.id,
        }, status_code=502)
    survey.stripe_payment_intent_id = session.id; db.commit()
    return _publish_redirect(request, session.url, survey.id)


__all__ = ['calculate_price', 'publish_interview', '_publish_redirect', 'publish_survey']
