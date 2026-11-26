"""Features / dashboard / queries."""

from sqlalchemy import and_
from sqlalchemy import case
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional
import os
from app.ai_growth.prediction import cached_recommendations_for_user
from app.jobs.service import enqueue_dashboard_recommendations_refresh_job
from app.models import Response
from app.models import ScreeningAttempt
from app.models import ScreeningQuestion
from app.models import Survey
from app.models import User


URGENCY_RANK = {
    "within_1_week":  3,
    "within_1_month": 2,
    "flexible":       1,
}


def _dashboard_candidate_limit() -> int:
    try:
        return int(os.environ.get("INSIGHTA_DASHBOARD_CANDIDATE_LIMIT", "100"))
    except ValueError:
        return 100


def _dashboard_scan_limit() -> int:
    """Maximum SQL-prefiltered rows inspected before legacy Python checks."""
    try:
        configured = int(os.environ.get("INSIGHTA_DASHBOARD_SCAN_LIMIT", "500"))
    except ValueError:
        configured = 500
    if configured <= 0:
        return 0
    candidate_limit = _dashboard_candidate_limit()
    return max(configured, candidate_limit if candidate_limit > 0 else 0)


def _dashboard_ai_recommendations_enabled() -> bool:
    # Cached AI ranking can be consumed without spending any new tokens.
    # New Claude calls are controlled separately by
    # INSIGHTA_DASHBOARD_AI_AUTO_REFRESH and are OFF by default.
    value = os.environ.get("INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _dashboard_ai_auto_refresh_enabled() -> bool:
    """Whether passive dashboard cache misses may enqueue new LLM work.

    This is deliberately disabled by default. A normal dashboard page view must
    not spend Anthropic tokens. Explicit AI endpoints/buttons can still call the
    LLM when the user asks for a forecast, recompute, generation, or analysis.
    """
    value = os.environ.get("INSIGHTA_DASHBOARD_AI_AUTO_REFRESH", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _dashboard_normalized(column):
    return func.lower(func.trim(func.coalesce(column, "")))


def _dashboard_empty_target_filter(column):
    normalized = _dashboard_normalized(column)
    return or_(normalized == "", normalized == "all")


def _dashboard_scalar_target_filter(column, user_value: Optional[str]):
    empty_filter = _dashboard_empty_target_filter(column)
    normalized_user = (user_value or "").strip().lower()
    if not normalized_user:
        return empty_filter
    return or_(empty_filter, _dashboard_normalized(column) == normalized_user)


def _dashboard_language_filter(user_languages: Optional[str]):
    empty_filter = _dashboard_empty_target_filter(Survey.target_language)
    languages = {
        language.strip().lower()
        for language in (user_languages or "").split(",")
        if language.strip()
    }
    if not languages:
        return empty_filter
    return or_(
        empty_filter,
        *[_dashboard_normalized(Survey.target_language) == language for language in sorted(languages)],
    )


def _dashboard_location_filter(user: User):
    empty_filter = _dashboard_empty_target_filter(Survey.target_state)
    locations = {
        value.strip().lower()
        for value in [getattr(user, "state", None), getattr(user, "current_province", None)]
        if value and value.strip()
    }
    if not locations:
        return empty_filter
    return or_(
        empty_filter,
        *[_dashboard_normalized(Survey.target_state) == location for location in sorted(locations)],
    )


def _dashboard_participation_format_filter(user_value: Optional[str]):
    normalized_user = (user_value or "").strip().lower()
    if not normalized_user or normalized_user in {"both", "any format", "hybrid"}:
        return None
    normalized = _dashboard_normalized(Survey.target_participation_format)
    return or_(
        normalized == "",
        normalized == "all",
        normalized == "both",
        normalized == "any format",
        normalized == "hybrid",
        normalized == normalized_user,
    )


def _dashboard_device_filter(user_value: Optional[str]):
    normalized_user = (user_value or "").strip().lower()
    normalized = _dashboard_normalized(Survey.target_device)
    unrestricted = or_(normalized == "", normalized == "all", normalized == "any")
    if normalized_user == "any":
        return None
    if not normalized_user:
        return unrestricted
    return or_(unrestricted, normalized == normalized_user)


def _dashboard_targeting_sql_filter(current_user: User):
    """Build the exact SQL-safe portion of participant targeting.

    Age and comma-separated tag overlap remain as a final Python check because
    their legacy formats are intentionally permissive. Everything else is
    pushed into SQL so the dashboard never needs to materialize every published
    survey just to reject it in Python.
    """
    from app.features.matching.eligibility import _education_rank

    # Keep this predicate conservative: every SQL condition here should match
    # the legacy Python helper exactly or be looser. Narrower filters can hide
    # valid studies before `_dashboard_survey_matches` gets a chance to run.
    user_edu_min = _education_rank(current_user.education_level, fallback=0)
    user_edu_max = _education_rank(current_user.education_level, fallback=999)
    filters = [
        or_(Survey.target_education_min.is_(None), Survey.target_education_min <= user_edu_min),
        or_(Survey.target_education_max.is_(None), Survey.target_education_max >= user_edu_max),
        _dashboard_scalar_target_filter(Survey.target_field, current_user.field),
        _dashboard_scalar_target_filter(Survey.target_status, current_user.status),
        _dashboard_location_filter(current_user),
        _dashboard_language_filter(current_user.language),
        _dashboard_scalar_target_filter(Survey.target_ethnicity, current_user.ethnicity),
        _dashboard_scalar_target_filter(Survey.target_sexual_orientation, current_user.sexual_orientation),
        _dashboard_scalar_target_filter(Survey.target_mental_health_diagnosis, current_user.mental_health_diagnosis),
        _dashboard_scalar_target_filter(Survey.target_physical_health_diagnosis, current_user.physical_health_diagnosis),
        _dashboard_scalar_target_filter(Survey.target_sport_type, current_user.sport_type),
        _dashboard_scalar_target_filter(Survey.target_sport_frequency, current_user.sport_frequency),
        _dashboard_scalar_target_filter(Survey.target_smoking, current_user.smoking),
        _dashboard_scalar_target_filter(Survey.target_cannabis_use, current_user.cannabis_use),
        _dashboard_scalar_target_filter(Survey.target_student_status, getattr(current_user, "student_status", None)),
        _dashboard_scalar_target_filter(Survey.target_international_domestic, getattr(current_user, "international_domestic", None)),
        _dashboard_scalar_target_filter(Survey.target_income_level, getattr(current_user, "income_level", None)),
    ]
    participation_filter = _dashboard_participation_format_filter(getattr(current_user, "participation_format", None))
    if participation_filter is not None:
        filters.append(participation_filter)
    device_filter = _dashboard_device_filter(getattr(current_user, "device_type", None))
    if device_filter is not None:
        filters.append(device_filter)
    return or_(Survey.payment_status == "admin_demo", and_(*filters))


def _dashboard_survey_matches(s: Survey, current_user: User) -> bool:
    """Final compatibility check for legacy targeting formats."""
    from app.features.matching.eligibility import _age_matches
    from app.features.matching.eligibility import _device_matches
    from app.features.matching.eligibility import _education_rank
    from app.features.matching.eligibility import _field_matches
    from app.features.matching.eligibility import _language_matches
    from app.features.matching.eligibility import _location_matches
    from app.features.matching.eligibility import _participation_format_matches
    from app.features.matching.eligibility import _tags_match

    if getattr(s, "payment_status", None) == "admin_demo":
        return True
    user_edu_min = _education_rank(current_user.education_level, fallback=0)
    user_edu_max = _education_rank(current_user.education_level, fallback=999)
    if not _age_matches(s.target_age_range, current_user): return False
    if s.target_education_min is not None and user_edu_min < s.target_education_min: return False
    if s.target_education_max is not None and user_edu_max > s.target_education_max: return False
    if not _field_matches(s.target_field, current_user.field): return False
    if not _field_matches(s.target_status, current_user.status): return False
    if not _location_matches(s.target_state, current_user): return False
    if not _language_matches(s.target_language, current_user.language): return False
    if not _field_matches(s.target_ethnicity, current_user.ethnicity): return False
    if not _field_matches(s.target_sexual_orientation, current_user.sexual_orientation): return False
    if not _field_matches(s.target_mental_health_diagnosis, current_user.mental_health_diagnosis): return False
    if not _field_matches(s.target_physical_health_diagnosis, current_user.physical_health_diagnosis): return False
    if not _field_matches(s.target_sport_type, current_user.sport_type): return False
    if not _field_matches(s.target_sport_frequency, current_user.sport_frequency): return False
    if not _field_matches(s.target_smoking, current_user.smoking): return False
    if not _field_matches(s.target_cannabis_use, current_user.cannabis_use): return False
    if not _field_matches(getattr(s, "target_student_status", None), getattr(current_user, "student_status", None)): return False
    if not _field_matches(getattr(s, "target_international_domestic", None), getattr(current_user, "international_domestic", None)): return False
    if not _tags_match(getattr(s, "target_experience_tags", None), getattr(current_user, "experience_tags", None)): return False
    if not _participation_format_matches(getattr(s, "target_participation_format", None), getattr(current_user, "participation_format", None)): return False
    if not _device_matches(getattr(s, "target_device", None), getattr(current_user, "device_type", None)): return False
    if not _field_matches(getattr(s, "target_income_level", None), getattr(current_user, "income_level", None)): return False
    if not _tags_match(getattr(s, "target_lifestyle_tags", None), getattr(current_user, "lifestyle_tags", None)): return False
    return True


def _dashboard_candidate_surveys(
    db: Session,
    current_user: User,
    *,
    exclude_existing_responses: bool,
) -> list[Survey]:
    """Fetch only a bounded, SQL-prefiltered dashboard candidate set."""
    # This replaces the old all-published-surveys scan. The route now asks the
    # database for likely matches first, then keeps the legacy Python pass only
    # for formats that are difficult to express safely in SQL.
    query = db.query(Survey).filter(
        Survey.status == "published",
        _dashboard_targeting_sql_filter(current_user),
    )
    if exclude_existing_responses:
        existing_response = db.query(Response.id).filter(
            Response.survey_id == Survey.id,
            Response.participant_id == current_user.id,
        )
        query = query.filter(~existing_response.exists())

    query = query.order_by(
        case((Survey.published_at.is_(None), 1), else_=0).asc(),
        Survey.published_at.desc(),
        Survey.created_at.desc(),
        Survey.id.desc(),
    )
    limit = _dashboard_candidate_limit()
    scan_limit = _dashboard_scan_limit()

    # Age and legacy comma-separated tag formats are checked in Python. Keep
    # that compatibility pass bounded too: without a scan ceiling, a sparse
    # match could still walk the entire published table page by page.
    effective_limit = limit if limit > 0 else None
    batch_size = max(100, min(500, (effective_limit or 100) * 2))
    offset = 0
    scanned = 0
    matched: list[Survey] = []
    while effective_limit is None or len(matched) < effective_limit:
        if scan_limit > 0 and scanned >= scan_limit:
            break
        page_limit = batch_size
        if scan_limit > 0:
            page_limit = min(page_limit, scan_limit - scanned)
        if page_limit <= 0:
            break
        rows = query.offset(offset).limit(page_limit).all()
        if not rows:
            break
        scanned += len(rows)
        matched.extend(
            survey for survey in rows
            if _dashboard_survey_matches(survey, current_user)
        )
        if len(rows) < page_limit:
            break
        offset += len(rows)

    if effective_limit is None:
        return matched
    return matched[:effective_limit]


def _cached_dashboard_recommendations(db: Session, surveys: list[Survey], user: User) -> dict[int, dict]:
    if not _dashboard_ai_recommendations_enabled() or not surveys:
        return {}

    # Reading an existing cache is token-free and remains allowed. Crucially, a
    # cache miss no longer creates LLM work unless an operator explicitly opts
    # back into passive auto-refresh.
    recommendations = cached_recommendations_for_user(db, surveys, user)
    if _dashboard_ai_auto_refresh_enabled():
        missing_ids = [survey.id for survey in surveys if survey.id not in recommendations]
        if missing_ids:
            enqueue_dashboard_recommendations_refresh_job(
                db,
                user_id=user.id,
                survey_ids=missing_ids,
            )
            db.flush()
    return recommendations


def _dashboard_screening_state(
    db: Session,
    survey_ids: list[int],
    participant_id: int,
) -> tuple[dict[int, bool], dict[int, bool]]:
    if not survey_ids:
        return {}, {}
    latest_rows = db.query(
        ScreeningQuestion.survey_id,
        func.max(ScreeningQuestion.version),
    ).filter(
        ScreeningQuestion.survey_id.in_(survey_ids),
        ScreeningQuestion.status == "approved",
    ).group_by(ScreeningQuestion.survey_id).all()
    latest_versions = {
        int(survey_id): int(version)
        for survey_id, version in latest_rows
        if version is not None
    }
    required = {survey_id: survey_id in latest_versions for survey_id in survey_ids}
    if not latest_versions:
        return required, {survey_id: True for survey_id in survey_ids}

    attempts = db.query(
        ScreeningAttempt.survey_id,
        ScreeningAttempt.version,
    ).filter(
        ScreeningAttempt.participant_id == participant_id,
        ScreeningAttempt.survey_id.in_(list(latest_versions)),
        ScreeningAttempt.status == "passed",
    ).all()
    passed_pairs = {(int(survey_id), int(version)) for survey_id, version in attempts}
    passed = {
        survey_id: (
            True if survey_id not in latest_versions
            else (survey_id, latest_versions[survey_id]) in passed_pairs
        )
        for survey_id in survey_ids
    }
    return required, passed


def _completed_counts_for_surveys(db: Session, survey_ids: list[int]) -> dict[int, int]:
    if not survey_ids:
        return {}
    return {
        survey_id: int(completed_count or 0)
        for survey_id, completed_count in db.query(Response.survey_id, func.count(Response.id))
        .filter(Response.survey_id.in_(survey_ids), Response.status == "completed")
        .group_by(Response.survey_id)
        .all()
    }


def _user_responses_for_surveys(db: Session, user_id: int, survey_ids: list[int]) -> dict[int, Response]:
    if not survey_ids:
        return {}
    result: dict[int, Response] = {}
    rows = db.query(Response).filter(
        Response.participant_id == user_id,
        Response.survey_id.in_(survey_ids),
    ).order_by(Response.id.asc()).all()
    for response in rows:
        result.setdefault(response.survey_id, response)
    return result


def _surveys_by_id(db: Session, survey_ids: list[int]) -> dict[int, Survey]:
    if not survey_ids:
        return {}
    return {
        survey.id: survey
        for survey in db.query(Survey).filter(Survey.id.in_(survey_ids)).all()
    }


__all__ = ['URGENCY_RANK', '_dashboard_candidate_limit', '_dashboard_scan_limit', '_dashboard_ai_recommendations_enabled', '_dashboard_ai_auto_refresh_enabled', '_dashboard_normalized', '_dashboard_empty_target_filter', '_dashboard_scalar_target_filter', '_dashboard_language_filter', '_dashboard_location_filter', '_dashboard_participation_format_filter', '_dashboard_device_filter', '_dashboard_targeting_sql_filter', '_dashboard_survey_matches', '_dashboard_candidate_surveys', '_cached_dashboard_recommendations', '_dashboard_screening_state', '_completed_counts_for_surveys', '_user_responses_for_surveys', '_surveys_by_id']
