"""Answer normalization and quality label conversion."""
from __future__ import annotations

from typing import Any
from typing import Iterable
from typing import Optional
from datetime import datetime
from datetime import timezone


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    return False

def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _duration_seconds_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if not start or not end:
        return None
    start_dt = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end_dt = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)
    seconds = (end_dt - start_dt).total_seconds()
    return seconds if seconds > 0 else None

def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()

def _answer_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value).strip()

def _label_from_score(score: float, fraud_risk: bool) -> str:
    if fraud_risk:
        return "fraud_risk"
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"

def _review_status_from_label(label: str, fraud_risk: bool) -> str:
    if fraud_risk:
        return "needs_review"
    if label == "medium":
        return "needs_review"
    if label == "low":
        return "needs_review"
    return "pending"

def _question_matches_keywords(question_text: str, keywords: Iterable[str]) -> bool:
    text = question_text.lower()
    return any(kw.lower() in text for kw in keywords)

def _answer_matches_patterns(answer_value: Any, patterns: Iterable[str]) -> bool:
    text = _normalize_text(_answer_text(answer_value))
    if not text:
        return False
    pattern_set = {p.lower() for p in patterns if p}
    if text in pattern_set:
        return True
    for pattern in pattern_set:
        if len(pattern) > 3 and pattern in text:
            return True
    return False
