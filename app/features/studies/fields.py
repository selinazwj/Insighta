"""Features / studies / fields."""

from fastapi import HTTPException
from typing import Optional
from app.models import Survey
from app.verification.assurance import TIER_BASIC
from app.verification.assurance import TIER_IDENTITY
from app.verification.assurance import TIER_TRUSTED


def _parse_optional_int(v) -> Optional[int]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _parse_optional_float(v) -> Optional[float]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _normalize_task_type(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"interview", "online_interview", "online-interview", "remote_interview"}:
        return "interview"
    if normalized in {"in_person", "in-person", "in_person_study"}:
        return "in_person"
    return "survey"


def _task_type_label(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if raw in {"online_interview", "online-interview", "remote_interview"}:
        return "Online interview"
    if raw in {"in_person", "in-person", "in_person_study"}:
        return "In-person study"
    task_type = _normalize_task_type(value)
    if task_type == "interview":
        return "Interview"
    if task_type == "in_person":
        return "In-person study"
    return "Survey"


def _uses_booking_flow(value: Optional[str]) -> bool:
    return _normalize_task_type(value) in {"interview", "in_person"}


RESEARCH_PARTICIPATION_DEMO_TITLE = "Understand the Motivations and Barriers to Participating in Online Surveys and Research Studies"


RESEARCH_PARTICIPATION_DEMO_CALENDLY_URL = "https://calendly.com/vfsa-bu/understanding-research-participation"


def _compact_text(value: Optional[str]) -> str:
    return "".join((value or "").split()).lower()


def _is_research_participation_demo_survey(survey: Survey) -> bool:
    compact_title = _compact_text(getattr(survey, "title", None))
    return (
        compact_title == _compact_text(RESEARCH_PARTICIPATION_DEMO_TITLE)
        or (
            "motivation" in compact_title
            and "barrier" in compact_title
            and "onlinesurveys" in compact_title
            and "researchstudies" in compact_title
        )
    )


def _survey_external_start_url(survey: Survey) -> str:
    if _is_research_participation_demo_survey(survey):
        return RESEARCH_PARTICIPATION_DEMO_CALENDLY_URL
    form_url = (getattr(survey, "form_url", None) or "").strip()
    return form_url if form_url and form_url != "__builtin__" else ""


def _survey_has_external_start_link(survey: Survey) -> bool:
    return bool(_survey_external_start_url(survey))


def _is_online_interview_survey(survey: Survey) -> bool:
    task_type = _normalize_task_type(getattr(survey, "task_type", None))
    participation_format = (getattr(survey, "target_participation_format", None) or "").strip().lower()
    # target_participation_format also recognizes listings created by the old
    # interview form, which accidentally stored online interviews as in_person.
    if participation_format in {"video interview", "online interview", "remote live session"}:
        return True
    if task_type != "interview":
        return False
    if _is_research_participation_demo_survey(survey):
        return True
    form_url = _survey_external_start_url(survey).lower()
    return (
        "calendly.com" in form_url
        or "zoom.us" in form_url
        or "meet.google.com" in form_url
    )


def _participant_study_type_label(survey: Survey) -> Optional[str]:
    task_type = _normalize_task_type(getattr(survey, "task_type", None))
    if _uses_booking_flow(getattr(survey, "task_type", None)) and _is_online_interview_survey(survey):
        return "Online interview"
    if task_type == "interview":
        participation_format = (getattr(survey, "target_participation_format", None) or "").strip().lower()
        if participation_format in {"in-person study", "in person", "in-person"}:
            return "In-person study"
        return "Interview"
    if task_type == "in_person":
        return "In-person study"
    return None


def _participant_study_action_label(survey: Survey) -> str:
    task_type = _normalize_task_type(getattr(survey, "task_type", None))
    if _uses_booking_flow(getattr(survey, "task_type", None)) and _is_online_interview_survey(survey):
        return "Schedule interview"
    if task_type == "interview":
        return "Book time"
    if task_type == "in_person":
        return "Book time"
    return "Take study"


def _clean_target(val: Optional[str]) -> str:
    return '' if not val or val.strip().lower() == 'all' else val


def _normalize_required_verification_tier(value: Optional[str]) -> str:
    normalized = (value or TIER_BASIC).strip().lower()
    if normalized == "tier_1":
        raise HTTPException(
            400,
            "Legacy ID-based assurance is no longer supported.",
        )
    if normalized not in {TIER_IDENTITY, TIER_TRUSTED, TIER_BASIC}:
        return TIER_BASIC
    return normalized


def _join_form_list_with_other(values: list[str], other_value: Optional[str]) -> Optional[str]:
    cleaned = []
    seen = set()
    for value in values:
        item = str(value or "").strip()
        if item and item.lower() != "all" and item not in seen:
            cleaned.append(item)
            seen.add(item)
    other = (other_value or "").strip()
    if other and other not in seen:
        cleaned.append(other)
    return "; ".join(cleaned) if cleaned else None


__all__ = ['_parse_optional_int', '_parse_optional_float', '_normalize_task_type', '_task_type_label', '_uses_booking_flow', 'RESEARCH_PARTICIPATION_DEMO_TITLE', 'RESEARCH_PARTICIPATION_DEMO_CALENDLY_URL', '_compact_text', '_is_research_participation_demo_survey', '_survey_external_start_url', '_survey_has_external_start_link', '_is_online_interview_survey', '_participant_study_type_label', '_participant_study_action_label', '_clean_target', '_normalize_required_verification_tier', '_join_form_list_with_other']
