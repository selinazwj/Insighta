"""Evaluation of approved versioned screening answers."""
from __future__ import annotations

from typing import Any
from typing import Iterable
from typing import Mapping

from .types import (
    ScreeningDecision,
)
from .normalization import (
    _split_values,
)

def evaluate_screening(
    questions: Iterable[Mapping[str, Any]],
    answers: Mapping[str, Any],
) -> ScreeningDecision:
    """Deterministically evaluate an approved, versioned question set."""
    reasons: list[str] = []
    missing = 0
    failed = 0
    for question in questions:
        attribute = str(question.get("attribute") or "")
        answer = str(answers.get(attribute) or "").strip()
        question_type = str(question.get("question_type") or "single")
        accepted = {value.lower() for value in _split_values(question.get("accepted_answers"))}
        if not answer:
            missing += 1
            reasons.append(f"Missing answer for {attribute}")
        elif question_type == "single_context":
            continue
        elif accepted and answer.lower() not in accepted:
            failed += 1
            reasons.append(f"Does not meet the {attribute.replace('_', ' ')} requirement")

    if missing:
        return ScreeningDecision(
            status="review",
            risk_score=min(100, 25 + missing * 15),
            reasons=tuple(reasons),
        )
    if failed:
        return ScreeningDecision(
            status="failed",
            risk_score=min(100, 20 + failed * 20),
            reasons=tuple(reasons),
        )
    return ScreeningDecision(status="passed", risk_score=0, reasons=())
