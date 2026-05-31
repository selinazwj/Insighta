"""Reusable targeting and profile matching for dashboard, jump, and prediction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models import Survey, User


EDUCATION_RANK = {
    "High School": 1,
    "Undergraduate": 2,
    "Graduate": 3,
    "PhD": 4,
}

# These fields can be used for explicit eligibility filtering, but they are not
# exposed as ranking explanations to avoid sensitive-attribute explanations.
SENSITIVE_TARGET_FIELDS = {
    "target_ethnicity",
    "target_sexual_orientation",
    "target_mental_health_diagnosis",
    "target_physical_health_diagnosis",
    "target_smoking",
    "target_cannabis_use",
}


@dataclass
class MatchResult:
    eligible: bool
    score: float
    matched_fields: list[str]
    missing_fields: list[str]
    failed_fields: list[str]


def education_rank(level: Optional[str], fallback: int) -> int:
    if not level:
        return fallback
    return EDUCATION_RANK.get(level, fallback)


def is_empty(val: Optional[str]) -> bool:
    return val is None or str(val).strip() == "" or str(val).strip().lower() in {"all", "any", "both"}


def norm(val: Optional[str]) -> str:
    return (val or "").strip().lower()


def split_tags(value: Optional[str]) -> set[str]:
    return {v.strip().lower() for v in (value or "").split(",") if v.strip()}


def field_match_score(target: Optional[str], user_val: Optional[str], wildcard_values: set[str] | None = None) -> tuple[bool, float, str]:
    wildcard_values = wildcard_values or {"all", "any", "both"}
    if target is None or str(target).strip() == "" or norm(target) in wildcard_values:
        return True, 1.0, "not_required"
    if not user_val:
        return True, 0.4, "missing_user_info"
    return (norm(target) == norm(user_val), 1.0 if norm(target) == norm(user_val) else 0.0, "matched" if norm(target) == norm(user_val) else "failed")


def language_match_score(target: Optional[str], user_languages: Optional[str]) -> tuple[bool, float, str]:
    if is_empty(target):
        return True, 1.0, "not_required"
    if not user_languages:
        return True, 0.4, "missing_user_info"
    user_list = split_tags(user_languages)
    ok = norm(target) in user_list
    return ok, 1.0 if ok else 0.0, "matched" if ok else "failed"


def tags_match_score(target_tags: Optional[str], user_tags: Optional[str]) -> tuple[bool, float, str]:
    if is_empty(target_tags):
        return True, 1.0, "not_required"
    if not user_tags:
        return True, 0.4, "missing_user_info"
    target_set, user_set = split_tags(target_tags), split_tags(user_tags)
    overlap = target_set & user_set
    if not target_set:
        return True, 1.0, "not_required"
    if not overlap:
        return False, 0.0, "failed"
    return True, min(1.0, 0.6 + 0.4 * len(overlap) / max(1, len(target_set))), "matched"


def survey_match_result(survey: Survey, user: User, strict: bool = True) -> MatchResult:
    scores: list[float] = []
    matched: list[str] = []
    missing: list[str] = []
    failed: list[str] = []

    def add(field_name: str, ok: bool, score: float, status: str):
        scores.append(score)
        safe_name = field_name.replace("target_", "")
        if status == "matched" and field_name not in SENSITIVE_TARGET_FIELDS:
            matched.append(safe_name)
        elif status == "missing_user_info" and field_name not in SENSITIVE_TARGET_FIELDS:
            missing.append(safe_name)
        elif not ok:
            failed.append(safe_name)

    mapping = [
        ("target_age_range", getattr(survey, "target_age_range", None), getattr(user, "age_range", None)),
        ("target_field", getattr(survey, "target_field", None), getattr(user, "field", None)),
        ("target_status", getattr(survey, "target_status", None), getattr(user, "status", None)),
        ("target_state", getattr(survey, "target_state", None), getattr(user, "state", None)),
        ("target_ethnicity", getattr(survey, "target_ethnicity", None), getattr(user, "ethnicity", None)),
        ("target_sexual_orientation", getattr(survey, "target_sexual_orientation", None), getattr(user, "sexual_orientation", None)),
        ("target_mental_health_diagnosis", getattr(survey, "target_mental_health_diagnosis", None), getattr(user, "mental_health_diagnosis", None)),
        ("target_physical_health_diagnosis", getattr(survey, "target_physical_health_diagnosis", None), getattr(user, "physical_health_diagnosis", None)),
        ("target_sport_type", getattr(survey, "target_sport_type", None), getattr(user, "sport_type", None)),
        ("target_sport_frequency", getattr(survey, "target_sport_frequency", None), getattr(user, "sport_frequency", None)),
        ("target_smoking", getattr(survey, "target_smoking", None), getattr(user, "smoking", None)),
        ("target_cannabis_use", getattr(survey, "target_cannabis_use", None), getattr(user, "cannabis_use", None)),
        ("target_student_status", getattr(survey, "target_student_status", None), getattr(user, "student_status", None)),
        ("target_year_in_school", getattr(survey, "target_year_in_school", None), getattr(user, "year_in_school", None)),
        ("target_international_domestic", getattr(survey, "target_international_domestic", None), getattr(user, "international_domestic", None)),
    ]

    eligible = True
    for name, target, value in mapping:
        ok, score, status = field_match_score(target, value)
        add(name, ok, score, status)
        if strict and not ok:
            eligible = False

    ok, score, status = language_match_score(getattr(survey, "target_language", None), getattr(user, "language", None))
    add("target_language", ok, score, status)
    if strict and not ok:
        eligible = False

    ok, score, status = tags_match_score(getattr(survey, "target_experience_tags", None), getattr(user, "experience_tags", None))
    add("target_experience_tags", ok, score, status)
    if strict and not ok:
        eligible = False

    ok, score, status = field_match_score(getattr(survey, "target_participation_format", None), getattr(user, "participation_format", None), {"all", "both", "any"})
    add("target_participation_format", ok, score, status)
    if strict and not ok:
        eligible = False

    ok, score, status = field_match_score(getattr(survey, "target_device", None), getattr(user, "device_type", None), {"all", "both", "any"})
    add("target_device", ok, score, status)
    if strict and not ok:
        eligible = False

    edu_scores = []
    user_min = education_rank(getattr(user, "education_level", None), 0)
    user_max = education_rank(getattr(user, "education_level", None), 999)
    if getattr(survey, "target_education_min", None) is not None:
        ok = user_min >= survey.target_education_min
        edu_scores.append(1.0 if ok else 0.0)
        if strict and not ok:
            eligible = False
            failed.append("education_min")
    if getattr(survey, "target_education_max", None) is not None:
        ok = user_max <= survey.target_education_max
        edu_scores.append(1.0 if ok else 0.0)
        if strict and not ok:
            eligible = False
            failed.append("education_max")
    if edu_scores:
        scores.append(sum(edu_scores) / len(edu_scores))
        if all(x >= 1.0 for x in edu_scores):
            matched.append("education")

    score = sum(scores) / len(scores) if scores else 1.0
    return MatchResult(eligible=eligible, score=round(max(0.0, min(1.0, score)), 4), matched_fields=matched[:8], missing_fields=missing[:8], failed_fields=failed[:8])


def survey_matches(survey: Survey, user: User) -> bool:
    return survey_match_result(survey, user, strict=True).eligible
