"""Features / matching / eligibility."""

from datetime import date
from datetime import datetime
from datetime import timezone
from typing import Optional
import re
from app.models import Survey
from app.models import User


EDUCATION_RANK = {
    "Below High School": 0,
    "High School": 1,
    "Undergraduate": 2,
    "Graduate": 3,
    "PhD": 4,
    "Postdoc": 5,
}


def _education_rank(level: Optional[str], fallback: int) -> int:
    if not level:
        return fallback
    return EDUCATION_RANK.get(level, fallback)


def _age_range_from_birth_date(birth_year: Optional[str], birth_month: Optional[str]) -> Optional[str]:
    if not birth_year:
        return None
    try:
        year = int(birth_year)
        month = int(birth_month or "1")
    except ValueError:
        return None
    today = datetime.now(timezone.utc)
    age = today.year - year - (1 if today.month < month else 0)
    if age < 18:
        return None
    if age <= 24:
        return "18-24"
    if age <= 34:
        return "25-34"
    if age <= 44:
        return "35-44"
    return "45+"


def _value_with_other(value: Optional[str], other_value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    other_value = (other_value or "").strip()
    if value in {"Other", "Prefer to self-describe"} and other_value:
        return f"{value}: {other_value}"
    return value or None


def _list_with_other(values: list[str], other_value: Optional[str]) -> list[str]:
    cleaned = [v.strip() for v in values if v and v.strip()]
    other_value = (other_value or "").strip()
    if "Other" in cleaned and other_value:
        cleaned.append(f"Other: {other_value}")
    return cleaned


def _is_empty(val: Optional[str]) -> bool:
    return val is None or val.strip() == "" or val.strip().lower() == "all"


def _field_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    if _is_empty(target):
        return True
    if not user_val:
        return False
    return target.strip().lower() == user_val.strip().lower()


FIELD_RELEVANCE_ALIASES = {
    "computer science & engineering": ["computer science", "engineering", "software", "developer", "coding", "programming", "technology", "tech"],
    "data science / ai": ["data science", "data", "ai", "artificial intelligence", "machine learning", "ml", "analytics", "algorithm"],
    "business & economics": ["business", "economics", "finance", "entrepreneurship", "startup", "management", "consumer", "market"],
    "marketing / design": ["marketing", "design", "brand", "ux", "user experience", "product", "advertising", "creative"],
    "medicine & healthcare": ["medicine", "healthcare", "health", "clinical", "patient", "care", "medical", "public health"],
    "psychology / neuroscience": ["psychology", "neuroscience", "mental health", "behavior", "cognition", "brain", "wellness"],
    "natural sciences": ["biology", "chemistry", "physics", "environment", "science", "lab", "ecology"],
    "social sciences": ["social science", "sociology", "anthropology", "political science", "community", "society", "policy"],
    "education": ["education", "teaching", "learning", "school", "student", "classroom", "curriculum"],
    "law / public policy": ["law", "legal", "policy", "government", "civic", "regulation", "justice"],
    "arts & humanities": ["arts", "humanities", "history", "literature", "philosophy", "culture", "music", "theater"],
    "communications / media": ["communications", "media", "journalism", "social media", "content", "creator", "news"],
    "architecture / design": ["architecture", "urban", "built environment", "space", "housing", "design"],
}


def _text_relevance_score(needles: list[str], haystack: str) -> float:
    if not needles or not haystack:
        return 0.0
    haystack = haystack.lower()
    hits = sum(1 for needle in needles if needle and needle.lower() in haystack)
    if hits <= 0:
        return 0.0
    return min(0.75, 0.25 + hits * 0.15)


def _field_relevance_score(survey: Survey, user: User) -> float:
    user_field = (getattr(user, "field", None) or "").strip()
    if not user_field:
        return 0.0
    survey_target = (getattr(survey, "target_field", None) or "").strip()
    user_field_clean = user_field.lower()
    survey_target_clean = survey_target.lower()
    if survey_target_clean and survey_target_clean != "all":
        if survey_target_clean == user_field_clean:
            return 1.0
        aliases = FIELD_RELEVANCE_ALIASES.get(user_field_clean, [])
        if survey_target_clean in aliases or any(term in survey_target_clean for term in aliases):
            return 0.85
        return 0.0

    aliases = FIELD_RELEVANCE_ALIASES.get(user_field_clean, [])
    user_terms = [user_field_clean] + aliases
    survey_text = " ".join([
        getattr(survey, "title", None) or "",
        getattr(survey, "description", None) or "",
        getattr(survey, "category", None) or "",
        getattr(survey, "target_niche_requirements", None) or "",
        getattr(survey, "participant_benefits", None) or "",
    ])
    score = _text_relevance_score(user_terms, survey_text)

    interest_text = (getattr(user, "profile_description", None) or "").strip()
    if interest_text:
        interest_terms = [
            token for token in re.findall(r"[A-Za-z][A-Za-z+/#-]{3,}", interest_text.lower())
            if token not in {"with", "that", "this", "from", "about", "study", "research", "interested"}
        ][:12]
        score = max(score, min(0.6, _text_relevance_score(interest_terms, survey_text)))
    return round(score, 4)


def _recommendation_sort_score(survey: Survey, user: User, recommendation: dict) -> float:
    completion_probability = float((recommendation or {}).get("completion_probability") or 0.0)
    field_fit = _field_relevance_score(survey, user)
    return round((completion_probability * 0.75) + (field_fit * 0.25), 4)


def _age_matches(target: Optional[str], user: User) -> bool:
    if _is_empty(target):
        return True
    target_clean = target.strip().lower()
    try:
        birth_year = int(getattr(user, "birth_year", None) or 0)
        birth_month = int(getattr(user, "birth_month", None) or 1)
        today = date.today()
        user_age = today.year - birth_year - ((today.month, today.day) < (birth_month, 1))
    except (TypeError, ValueError):
        user_age = None
    numbers = [int(n) for n in re.findall(r"\d+", target_clean)]
    if user_age is not None and numbers:
        if len(numbers) >= 2:
            return numbers[0] <= user_age <= numbers[1]
        if "+" in target_clean or "older" in target_clean:
            return user_age >= numbers[0]
        if "under" in target_clean or "below" in target_clean:
            return user_age <= numbers[0]
    return _field_matches(target, getattr(user, "age_range", None))


def _location_matches(target: Optional[str], user: User) -> bool:
    if _is_empty(target):
        return True
    target_clean = target.strip().lower()
    user_values = [
        getattr(user, "state", None),
        getattr(user, "current_province", None),
    ]
    return any((value or "").strip().lower() == target_clean for value in user_values)


def _language_matches(target: Optional[str], user_languages: Optional[str]) -> bool:
    if _is_empty(target):
        return True
    if not user_languages:
        return False
    user_list = [lang.strip().lower() for lang in user_languages.split(",") if lang.strip()]
    return target.strip().lower() in user_list


def _tags_match(target_tags: Optional[str], user_tags: Optional[str]) -> bool:
    if _is_empty(target_tags):
        return True
    if not user_tags:
        return False
    target_set = {t.strip().lower() for t in target_tags.split(",") if t.strip()}
    user_set = {t.strip().lower() for t in user_tags.split(",") if t.strip()}
    return bool(target_set & user_set)


def _participation_format_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    target_clean = (target or "").strip().lower()
    user_clean = (user_val or "").strip().lower()
    if _is_empty(target) or target_clean in {"both", "any format", "hybrid"}:
        return True
    if not user_clean:
        return True
    if user_clean in {"both", "any format", "hybrid"}:
        return True
    return target_clean == user_clean


def _device_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    if _is_empty(target) or (target and target.strip().lower() == "any"):
        return True
    if not user_val:
        return False
    if user_val.strip().lower() == "any":
        return True
    return target.strip().lower() == user_val.strip().lower()


__all__ = ['EDUCATION_RANK', '_education_rank', '_age_range_from_birth_date', '_value_with_other', '_list_with_other', '_is_empty', '_field_matches', 'FIELD_RELEVANCE_ALIASES', '_text_relevance_score', '_field_relevance_score', '_recommendation_sort_score', '_age_matches', '_location_matches', '_language_matches', '_tags_match', '_participation_format_matches', '_device_matches']
