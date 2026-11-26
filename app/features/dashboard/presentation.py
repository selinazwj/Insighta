"""Features / dashboard / presentation."""

from sqlalchemy.orm import Session
from typing import Optional
import json
from app.models import Response
from app.models import Survey
from app.models import User


def _verification_satisfies_with_tier(user: User, survey: Survey, actual_tier: Optional[str]) -> bool:
    from app.features.verification.service import _email_domain
    from app.features.verification.service import _normalize_domains
    from app.features.verification.service import _tier_satisfies
    from app.features.verification.service import _verified_email_for

    required_tier = getattr(survey, "required_verification_tier", None) or "tier_3"
    if not _tier_satisfies(actual_tier, required_tier):
        return False
    domains = _normalize_domains(getattr(survey, "required_email_domains", None))
    if domains and _email_domain(_verified_email_for(user)) not in domains:
        return False
    return True


def _dashboard_share_paths(db: Session, surveys: list[Survey]) -> dict[int, str]:
    """Ensure dashboard cards have share slugs without per-card commits."""
    from app.features.studies.sharing import _new_share_slug

    missing = [survey for survey in surveys if not getattr(survey, "share_slug", None)]
    while missing:
        generated: dict[int, str] = {}
        used = set()
        for survey in missing:
            slug = _new_share_slug()
            while slug in used:
                slug = _new_share_slug()
            generated[survey.id] = slug
            used.add(slug)
        collisions = {
            slug for (slug,) in db.query(Survey.share_slug).filter(Survey.share_slug.in_(list(used))).all()
            if slug
        }
        still_missing = []
        for survey in missing:
            slug = generated[survey.id]
            if slug in collisions:
                still_missing.append(survey)
            else:
                survey.share_slug = slug
        missing = still_missing
    if surveys:
        db.flush()
    return {survey.id: f"/r/{survey.share_slug}" for survey in surveys}


def _participant_survey_payload(
    s: Survey,
    db: Session,
    current_user: User,
    user_response: Optional[Response] = None,
    user_response_loaded: bool = False,
    completed_count: Optional[int] = None,
    verification_satisfied: Optional[bool] = None,
    screening_required: Optional[bool] = None,
    screening_passed: Optional[bool] = None,
    share_path: Optional[str] = None,
) -> dict:
    from app.features.bookings.service import _booking_calendar_path
    from app.features.bookings.service import _booking_google_calendar_links
    from app.features.bookings.slots import _parse_booking_slots
    from app.features.screening.service import _approved_screening_questions
    from app.features.screening.service import _screening_passed
    from app.features.studies.fields import _is_online_interview_survey
    from app.features.studies.fields import _normalize_task_type
    from app.features.studies.fields import _participant_study_action_label
    from app.features.studies.fields import _participant_study_type_label
    from app.features.studies.fields import _survey_external_start_url
    from app.features.studies.fields import _uses_booking_flow
    from app.features.studies.sharing import _survey_share_path
    from app.features.verification.service import _verification_satisfies

    if completed_count is None:
        completed_count = db.query(Response).filter(
            Response.survey_id == s.id, Response.status == "completed"
        ).count()
    if user_response is None and not user_response_loaded:
        user_response = db.query(Response).filter(
            Response.survey_id == s.id, Response.participant_id == current_user.id
        ).first()

    category_images = {
        "research": "/static/psych.jpg", "life": "/static/campus_life.jpg",
        "clubs": "/static/fb.jpg", "market": "/static/habit.png",
        "academic": "/static/r2.jpg", "other": "/static/food.jpeg"
    }
    form_link = _survey_external_start_url(s)
    response_status = user_response.status if user_response else None
    is_live_study = _uses_booking_flow(getattr(s, "task_type", None))
    display_reward = getattr(s, "admin_display_reward_amount", None)
    if display_reward is None:
        display_reward = s.reward_amount
    availability_slots = []
    if getattr(s, "availability_slots", None):
        try:
            parsed_slots = json.loads(s.availability_slots)
            if isinstance(parsed_slots, list):
                availability_slots = parsed_slots
        except Exception:
            availability_slots = []

    # ive added this for verification — tell the participant up front if this
    # study needs a stronger tier or a specific email domain they dont have yet
    required_tier = getattr(s, "required_verification_tier", None) or "tier_3"
    if verification_satisfied is None:
        verification_satisfied = _verification_satisfies(current_user, s, db)
    if screening_required is None:
        screening_questions = _approved_screening_questions(db, s.id)
        screening_required = bool(screening_questions)
    if screening_passed is None:
        screening_passed = _screening_passed(db, s.id, current_user.id) if screening_required else True

    return {
        "id": s.id,
        "title": s.title,
        "desc": s.description,
        "niche": getattr(s, "target_niche_requirements", None),
        "benefits": getattr(s, "participant_benefits", None),
        "link": form_link,
        "share_path": share_path or _survey_share_path(db, s, commit=True),
        "type": _normalize_task_type(getattr(s, "task_type", None)),
        "format": getattr(s, "target_participation_format", None),
        "type_label": _participant_study_type_label(s),
        "action_label": _participant_study_action_label(s),
        "category": s.category,
        "time": f"{s.estimated_time} min",
        "reward": f"${display_reward:.2f}",
        "responses": f"{completed_count}/{s.target_responses}",
        "img": s.image_url if s.image_url else category_images.get(s.category, "/static/psych.jpg"),
        "is_started": response_status == "started",
        "is_completed": response_status == "completed",
        "is_skipped": response_status == "skipped",
        "is_live_study": is_live_study,
        "is_online_interview": _is_online_interview_survey(s),
        "awaiting_interview": bool(
            is_live_study and response_status not in {None, "completed", "skipped"}
        ),
        "status": response_status,
        "booking_slot": getattr(user_response, "booking_slot", None) if user_response else None,
        "calendar_url": _booking_calendar_path(s, user_response),
        "google_calendar_links": _booking_google_calendar_links(s, user_response),
        "booking_slots": _parse_booking_slots(getattr(user_response, "booking_slot", None)) if user_response else [],
        "availability_slots": availability_slots,
        "has_native_booking": bool(is_live_study and availability_slots),
        "session_count": getattr(s, "session_count", None),
        "sessions_per_week": getattr(s, "sessions_per_week", None),
        "urgency": getattr(s, 'urgency_level', None) or 'flexible',
        "incentive_type": getattr(s, 'incentive_type', None),
        "required_verification_tier": required_tier,
        "required_email_domains": getattr(s, "required_email_domains", None) or "",
        "verification_satisfied": verification_satisfied,
        "screening_required": screening_required,
        "screening_passed": screening_passed,
        "screening_url": f"/surveys/{s.id}/screening" if screening_required else "",
    }


__all__ = ['_verification_satisfies_with_tier', '_dashboard_share_paths', '_participant_survey_payload']
