"""Features / dashboard / routes."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from fastapi import Depends
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import json
from app.bootstrap.runtime import logger
from app.bootstrap.runtime import templates
from app.bootstrap.settings import BASE_URL
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Response
from app.models import Survey
from app.models import User
from app.observability import request_duration_ms
from app.observability import request_timer_start


def dashboard(
    request: Request,
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.auth.navigation import _is_mobile_request
    from app.features.dashboard.presentation import _dashboard_share_paths
    from app.features.dashboard.presentation import _participant_survey_payload
    from app.features.dashboard.presentation import _verification_satisfies_with_tier
    from app.features.dashboard.queries import _cached_dashboard_recommendations
    from app.features.dashboard.queries import _completed_counts_for_surveys
    from app.features.dashboard.queries import _dashboard_ai_recommendations_enabled
    from app.features.dashboard.queries import _dashboard_candidate_surveys
    from app.features.dashboard.queries import _dashboard_screening_state
    from app.features.dashboard.queries import _user_responses_for_surveys
    from app.features.matching.eligibility import _field_relevance_score
    from app.features.matching.eligibility import _recommendation_sort_score
    from app.features.verification.service import _current_assurance_tier

    if _is_mobile_request(request):
        tab = request.query_params.get("tab")
        mobile_url = f"/dashboard/mobile?tab={tab}" if tab in {"home", "earnings", "profile"} else "/dashboard/mobile"
        return RedirectResponse(mobile_url, status_code=302)
    dashboard_started_at = request_timer_start()
    matched = _dashboard_candidate_surveys(
        db,
        current_user,
        exclude_existing_responses=True,
    )

    # Use cached Claude recommendations only; refresh missing cache in the job runner.
    recommendation_map = _cached_dashboard_recommendations(db, matched, current_user)
    matched.sort(
        key=lambda s: (
            _recommendation_sort_score(s, current_user, recommendation_map.get(s.id, {})),
            s.published_at.timestamp() if s.published_at else 0,
        ),
        reverse=True,
    )

    surveys_data = []
    matched_ids = [s.id for s in matched]
    completed_counts = _completed_counts_for_surveys(db, matched_ids)
    user_responses = _user_responses_for_surveys(db, current_user.id, matched_ids)
    screening_required_map, screening_passed_map = _dashboard_screening_state(
        db,
        matched_ids,
        current_user.id,
    )
    actual_tier = _current_assurance_tier(db, current_user)
    share_paths = _dashboard_share_paths(db, matched)
    for s in matched:
        llm_rec = recommendation_map.get(s.id, {})
        user_response = user_responses.get(s.id)
        payload = _participant_survey_payload(
            s,
            db,
            current_user,
            user_response,
            user_response_loaded=True,
            completed_count=completed_counts.get(s.id, 0),
            verification_satisfied=_verification_satisfies_with_tier(current_user, s, actual_tier),
            screening_required=screening_required_map.get(s.id, False),
            screening_passed=screening_passed_map.get(s.id, True),
            share_path=share_paths.get(s.id),
        )
        payload.update({
            "completion_probability": llm_rec.get("completion_probability"),
            "field_fit_score": _field_relevance_score(s, current_user),
            "recommendation_score": _recommendation_sort_score(s, current_user, llm_rec),
            "ai_confidence": llm_rec.get("confidence"),
            "why_recommended": (llm_rec.get("top_reasons") or [])[:3],
            "risk_reasons": (llm_rec.get("risk_reasons") or [])[:3],
            "recommended_action": llm_rec.get("recommended_action"),
            "ranking_note": llm_rec.get("ranking_note"),
            "model_version": llm_rec.get("model_version"),
        })
        surveys_data.append(payload)

    if timezone_offset is not None:
        user_tz = timezone(timedelta(minutes=-timezone_offset))
        now_user = datetime.now(user_tz)
        today_start_user = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_user.astimezone(timezone.utc)
    else:
        now_utc = datetime.now(timezone.utc)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    completed_today = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= today_start_utc
    ).count()

    pending_earnings = getattr(current_user, 'pending_earnings', 0.0) or 0.0
    total_withdrawn = getattr(current_user, 'total_withdrawn', 0.0) or 0.0
    db.commit()

    logger.info(
        "dashboard_build path=/dashboard candidates=%s cache_hits=%s ai_enabled=%s duration_ms=%.2f",
        len(matched),
        len(recommendation_map),
        _dashboard_ai_recommendations_enabled(),
        request_duration_ms(dashboard_started_at),
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request, "surveys": surveys_data,
            "completed_today": completed_today, "total_earned": pending_earnings,
            "total_withdrawn": total_withdrawn, "pending_earnings": pending_earnings,
            "available_surveys": len(surveys_data), "current_user": current_user,
            "stripe_onboarding_complete": getattr(current_user, 'stripe_onboarding_complete', 'false'),
        }
    )


def dashboard_mobile(
    request: Request,
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.bookings.slots import _parse_booking_slots
    from app.features.dashboard.presentation import _dashboard_share_paths
    from app.features.dashboard.queries import _cached_dashboard_recommendations
    from app.features.dashboard.queries import _completed_counts_for_surveys
    from app.features.dashboard.queries import _dashboard_ai_recommendations_enabled
    from app.features.dashboard.queries import _dashboard_candidate_surveys
    from app.features.dashboard.queries import _user_responses_for_surveys
    from app.features.matching.eligibility import _field_relevance_score
    from app.features.matching.eligibility import _recommendation_sort_score
    from app.features.studies.fields import _normalize_task_type
    from app.features.studies.fields import _participant_study_action_label
    from app.features.studies.fields import _participant_study_type_label
    from app.features.studies.fields import _survey_external_start_url
    from app.features.studies.sharing import _survey_share_path

    dashboard_started_at = request_timer_start()
    # Mobile intentionally keeps started/skipped responses visible; completed
    # studies are filtered after the single batched response lookup below.
    matched = _dashboard_candidate_surveys(
        db,
        current_user,
        exclude_existing_responses=False,
    )

    # Use cached Claude recommendations only; refresh missing cache in the job runner.
    recommendation_map = _cached_dashboard_recommendations(db, matched, current_user)
    matched.sort(
        key=lambda s: (
            _recommendation_sort_score(s, current_user, recommendation_map.get(s.id, {})),
            s.published_at.timestamp() if s.published_at else 0,
        ),
        reverse=True,
    )

    category_images = {
        "research": "/static/psych.jpg", "life": "/static/campus_life.jpg",
        "clubs": "/static/fb.jpg", "market": "/static/habit.png",
        "academic": "/static/r2.jpg", "other": "/static/food.jpeg"
    }

    surveys_data = []
    matched_ids = [s.id for s in matched]
    completed_counts = _completed_counts_for_surveys(db, matched_ids)
    user_responses = _user_responses_for_surveys(db, current_user.id, matched_ids)
    share_paths = _dashboard_share_paths(db, matched)
    for s in matched:
        llm_rec = recommendation_map.get(s.id, {})
        completed_cnt = completed_counts.get(s.id, 0)
        user_response = user_responses.get(s.id)
        is_completed = user_response and user_response.status == "completed"
        # ive added this — completed studies only show in My Studies, not the mobile home feed
        if is_completed:
            continue
        form_link = _survey_external_start_url(s)
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
        surveys_data.append({
            "id": s.id, "title": s.title, "desc": s.description,
            "niche": getattr(s, "target_niche_requirements", None),
            "benefits": getattr(s, "participant_benefits", None),
            "link": form_link,
            "share_path": share_paths.get(s.id) or _survey_share_path(db, s, commit=False),
            "type": _normalize_task_type(getattr(s, "task_type", None)),
            "format": getattr(s, "target_participation_format", None),
            "type_label": _participant_study_type_label(s),
            "action_label": _participant_study_action_label(s),
            "category": s.category, "time": f"{s.estimated_time} min",
            "reward": f"${display_reward:.2f}",
            "responses": f"{completed_cnt}/{s.target_responses}",
            "img": s.image_url if s.image_url else category_images.get(s.category, "/static/psych.jpg"),
            "is_completed": is_completed,
            "booking_slot": getattr(user_response, "booking_slot", None) if user_response else None,
            "booking_slots": _parse_booking_slots(getattr(user_response, "booking_slot", None)) if user_response else [],
            "availability_slots": availability_slots,
            "session_count": getattr(s, "session_count", None),
            "sessions_per_week": getattr(s, "sessions_per_week", None),
            "urgency": getattr(s, 'urgency_level', None) or 'flexible',
            "incentive_type": getattr(s, 'incentive_type', None),
            "completion_probability": llm_rec.get("completion_probability"),
            "field_fit_score": _field_relevance_score(s, current_user),
            "recommendation_score": _recommendation_sort_score(s, current_user, llm_rec),
            "ai_confidence": llm_rec.get("confidence"),
            "why_recommended": (llm_rec.get("top_reasons") or [])[:3],
            "risk_reasons": (llm_rec.get("risk_reasons") or [])[:3],
            "recommended_action": llm_rec.get("recommended_action"),
            "ranking_note": llm_rec.get("ranking_note"),
            "model_version": llm_rec.get("model_version"),
        })

    pending_earnings = getattr(current_user, 'pending_earnings', 0.0) or 0.0
    total_withdrawn = getattr(current_user, 'total_withdrawn', 0.0) or 0.0

    completed_today = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
    ).count()
    db.commit()

    logger.info(
        "dashboard_build path=/dashboard/mobile candidates=%s visible=%s cache_hits=%s ai_enabled=%s duration_ms=%.2f",
        len(matched),
        len(surveys_data),
        len(recommendation_map),
        _dashboard_ai_recommendations_enabled(),
        request_duration_ms(dashboard_started_at),
    )

    return templates.TemplateResponse("dashboard_mobile.html", {
        "request": request,
        "surveys": surveys_data,
        "completed_today": completed_today,
        "pending_earnings": pending_earnings,
        "total_withdrawn": total_withdrawn,
        "available_surveys": len(surveys_data),
        "current_user": current_user,
        "stripe_onboarding_complete": getattr(current_user, 'stripe_onboarding_complete', 'false'),
        "researcher_desktop_url": BASE_URL,
        "researcher_desktop_host": BASE_URL.replace("https://", "").replace("http://", ""),
    })


def get_dashboard_stats(
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if timezone_offset is not None:
        user_tz = timezone(timedelta(minutes=-timezone_offset))
        now_user = datetime.now(user_tz)
        today_start_user = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_user.astimezone(timezone.utc)
    else:
        now_utc = datetime.now(timezone.utc)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    completed_today = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= today_start_utc
    ).count()

    completed_responses = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None)
    ).all()

    total_earned = 0.0
    for resp in completed_responses:
        survey = db.query(Survey).filter(Survey.id == resp.survey_id).first()
        if survey:
            total_earned += survey.reward_amount

    return JSONResponse({"completed_today": completed_today, "total_earned": total_earned})


__all__ = ['dashboard', 'dashboard_mobile', 'get_dashboard_stats']
