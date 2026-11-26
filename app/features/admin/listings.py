"""Features / admin / listings."""

from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.features.studies.fields import RESEARCH_PARTICIPATION_DEMO_CALENDLY_URL
from app.features.studies.fields import RESEARCH_PARTICIPATION_DEMO_TITLE
from app.models import Response
from app.models import Survey
from app.models import User


async def admin_list_listings(
    request: Request,
    admin_key: str = Query(None),
    q: str = Query(""),
    db: Session = Depends(get_db),
):
    from app.features.admin.access import _admin_key_matches
    from app.features.studies.fields import _normalize_task_type
    from app.features.studies.fields import _task_type_label
    from app.features.studies.sharing import _ensure_survey_share_slug
    from app.features.studies.sharing import _survey_share_url

    if not _admin_key_matches(admin_key): raise HTTPException(403, "Unauthorized")
    query = db.query(Survey).order_by(Survey.created_at.desc())
    search = (q or "").strip()
    if search:
        query = query.filter(Survey.title.ilike(f"%{search}%"))
    surveys = query.limit(200).all()
    slugs_changed = False
    for s in surveys:
        if not getattr(s, "share_slug", None):
            _ensure_survey_share_slug(db, s)
            slugs_changed = True
    if slugs_changed:
        db.commit()
    publisher_ids = {s.publisher_id for s in surveys if s.publisher_id}
    publishers = {}
    if publisher_ids:
        publishers = {u.id: u for u in db.query(User).filter(User.id.in_(publisher_ids)).all()}
    result = []
    for s in surveys:
        completed_count = db.query(Response).filter(
            Response.survey_id == s.id,
            Response.status == "completed",
        ).count()
        publisher = publishers.get(s.publisher_id)
        result.append({
            "id": s.id,
            "title": s.title,
            "description": s.description,
            "status": s.status,
            "payment_status": s.payment_status,
            "task_type": _normalize_task_type(getattr(s, "task_type", None)),
            "task_type_label": _task_type_label(getattr(s, "task_type", None)),
            "category": s.category,
            "reward_amount": s.reward_amount,
            "admin_display_reward_amount": getattr(s, "admin_display_reward_amount", None),
            "target_responses": s.target_responses,
            "completed_count": completed_count,
            "publisher_email": publisher.email if publisher else "unknown",
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "published_at": s.published_at.isoformat() if s.published_at else None,
            "share_url": _survey_share_url(request, db, s),
        })
    return JSONResponse(result)


async def admin_delete_listing(survey_id: int, request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.studies.lifecycle import _delete_survey_tree

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")): raise HTTPException(403, "Unauthorized")
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    deleted = {"id": survey.id, "title": survey.title}
    _delete_survey_tree(db, survey)
    return JSONResponse({"success": True, "deleted": deleted})


async def admin_delete_listings_by_title(request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.studies.lifecycle import _delete_survey_tree

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")): raise HTTPException(403, "Unauthorized")
    titles = [str(t).strip() for t in (body.get("titles") or []) if str(t).strip()]
    if not titles:
        raise HTTPException(400, "No titles provided")
    surveys = db.query(Survey).filter(Survey.title.in_(titles)).all()
    deleted = []
    for survey in surveys:
        deleted.append({"id": survey.id, "title": survey.title})
        _delete_survey_tree(db, survey)
    return JSONResponse({"success": True, "deleted": deleted})


async def admin_reset_research_participation_demo(request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.admin.access import _get_admin_publisher_user
    from app.features.studies.fields import _compact_text
    from app.features.studies.fields import _participant_study_type_label
    from app.features.studies.lifecycle import _delete_survey_tree
    from app.features.studies.sharing import _ensure_survey_share_slug
    from app.features.studies.sharing import _survey_share_url

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")):
        raise HTTPException(403, "Unauthorized")

    title = RESEARCH_PARTICIPATION_DEMO_TITLE
    calendly_url = RESEARCH_PARTICIPATION_DEMO_CALENDLY_URL
    title_compact = _compact_text(title)
    candidates = db.query(Survey).filter(
        Survey.title.ilike("%Motivations%"),
        Survey.title.ilike("%Barriers%"),
        Survey.title.ilike("%Research%"),
    ).order_by(Survey.created_at.desc()).all()
    existing = []
    for survey in candidates:
        survey_title_compact = _compact_text(survey.title)
        if survey_title_compact == title_compact or (
            "motivation" in survey_title_compact
            and "barrier" in survey_title_compact
            and "onlinesurveys" in survey_title_compact
            and "researchstudies" in survey_title_compact
        ):
            existing.append(survey)
    source = existing[0] if existing else None
    admin_user = _get_admin_publisher_user(db)

    source_data = {
        "description": (
            getattr(source, "description", None)
            or "Help us understand what motivates people to participate in online surveys and research studies, what barriers get in the way, and what would make research participation feel clearer, safer, and more worthwhile."
        ),
        "category": getattr(source, "category", None) or "research",
        "estimated_time": getattr(source, "estimated_time", None) or 30,
        "reward_amount": getattr(source, "reward_amount", None) or 0.0,
        "admin_display_reward_amount": getattr(source, "admin_display_reward_amount", None),
        "target_responses": getattr(source, "target_responses", None) or 50,
        "target_age_range": getattr(source, "target_age_range", None),
        "target_education_min": getattr(source, "target_education_min", None),
        "target_education_max": getattr(source, "target_education_max", None),
        "target_field": getattr(source, "target_field", None),
        "target_status": getattr(source, "target_status", None),
        "target_state": getattr(source, "target_state", None),
        "target_language": getattr(source, "target_language", None),
        "target_ethnicity": getattr(source, "target_ethnicity", None),
        "target_sexual_orientation": getattr(source, "target_sexual_orientation", None),
        "target_mental_health_diagnosis": getattr(source, "target_mental_health_diagnosis", None),
        "target_physical_health_diagnosis": getattr(source, "target_physical_health_diagnosis", None),
        "target_sport_type": getattr(source, "target_sport_type", None),
        "target_sport_frequency": getattr(source, "target_sport_frequency", None),
        "target_smoking": getattr(source, "target_smoking", None),
        "target_cannabis_use": getattr(source, "target_cannabis_use", None),
        "target_student_status": getattr(source, "target_student_status", None),
        "target_international_domestic": getattr(source, "target_international_domestic", None),
        "target_experience_tags": getattr(source, "target_experience_tags", None),
        "target_device": getattr(source, "target_device", None),
        "target_income_level": getattr(source, "target_income_level", None),
        "target_lifestyle_tags": getattr(source, "target_lifestyle_tags", None),
        "target_niche_requirements": getattr(source, "target_niche_requirements", None),
        "image_url": getattr(source, "image_url", None),
        "share_slug": getattr(source, "share_slug", None),
        "incentive_type": getattr(source, "incentive_type", None) or "cash",
        "raffle_prize_type": getattr(source, "raffle_prize_type", None),
        "session_count": None,
        "sessions_per_week": None,
    }
    if source_data["admin_display_reward_amount"] is None:
        source_data["admin_display_reward_amount"] = source_data["reward_amount"]

    deleted = []
    for survey in existing:
        deleted.append({"id": survey.id, "title": survey.title})
        _delete_survey_tree(db, survey)

    survey = Survey(
        publisher_id=admin_user.id,
        title=title,
        description=source_data["description"],
        form_url=calendly_url,
        task_type="interview",
        category=source_data["category"],
        estimated_time=source_data["estimated_time"],
        reward_amount=source_data["reward_amount"],
        admin_display_reward_amount=source_data["admin_display_reward_amount"],
        per_person_gross=0.0,
        total_budget=0.0,
        commission_rate=0.0,
        payment_status="admin_demo",
        target_responses=source_data["target_responses"],
        status="published",
        published_at=datetime.utcnow(),
        target_age_range=source_data["target_age_range"],
        target_education_min=source_data["target_education_min"],
        target_education_max=source_data["target_education_max"],
        target_field=source_data["target_field"],
        target_status=source_data["target_status"],
        target_state=source_data["target_state"],
        target_language=source_data["target_language"],
        target_ethnicity=source_data["target_ethnicity"],
        target_sexual_orientation=source_data["target_sexual_orientation"],
        target_mental_health_diagnosis=source_data["target_mental_health_diagnosis"],
        target_physical_health_diagnosis=source_data["target_physical_health_diagnosis"],
        target_sport_type=source_data["target_sport_type"],
        target_sport_frequency=source_data["target_sport_frequency"],
        target_smoking=source_data["target_smoking"],
        target_cannabis_use=source_data["target_cannabis_use"],
        target_student_status=source_data["target_student_status"],
        target_year_in_school=None,
        target_international_domestic=source_data["target_international_domestic"],
        target_experience_tags=source_data["target_experience_tags"],
        target_participation_format="Video interview",
        target_device=source_data["target_device"],
        target_income_level=source_data["target_income_level"],
        target_lifestyle_tags=source_data["target_lifestyle_tags"],
        target_niche_requirements=source_data["target_niche_requirements"],
        incentive_type=source_data["incentive_type"],
        raffle_prize_type=source_data["raffle_prize_type"],
        image_url=source_data["image_url"],
        share_slug=source_data["share_slug"],
        session_count=source_data["session_count"],
        sessions_per_week=source_data["sessions_per_week"],
        availability_slots=None,
    )
    db.add(survey)
    if not survey.share_slug:
        _ensure_survey_share_slug(db, survey)
    db.commit()
    db.refresh(survey)
    return JSONResponse({
        "success": True,
        "deleted": deleted,
        "created": {
            "id": survey.id,
            "title": survey.title,
            "share_url": _survey_share_url(request, db, survey),
            "start_url": survey.form_url,
            "type_label": _participant_study_type_label(survey),
        },
    })


__all__ = ['admin_list_listings', 'admin_delete_listing', 'admin_delete_listings_by_title', 'admin_reset_research_participation_demo']
