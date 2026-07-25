"""Deterministic participation decisions built from separate trust dimensions.

This module deliberately does not collect identity documents. It combines
already-derived statuses and produces a recommendation plus an audit snapshot.
AI-generated screening wording and AI quality hints never make the final
decision on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


ALLOW = "allow"
REVIEW = "review"
BLOCK = "block"


@dataclass(frozen=True)
class ParticipationInputs:
    human_status: str
    human_risk_score: int
    eligibility_status: str = "not_required"
    assurance_status: str = "passed"
    quality_status: str = "not_available"
    quality_score: Optional[float] = None
    fraud_risk: bool = False


@dataclass(frozen=True)
class ParticipationRecommendation:
    outcome: str
    reasons: tuple[str, ...]


def recommend_participation(
    inputs: ParticipationInputs,
    *,
    stage: str,
) -> ParticipationRecommendation:
    """Return a transparent gate recommendation.

    Start-time failures are blocking because the participant has not met a
    prerequisite. Completion/payout anomalies are held for human review rather
    than automatically rejected.
    """
    reasons: list[str] = []
    normalized_stage = (stage or "start").strip().lower()

    if inputs.assurance_status != "passed":
        reasons.append("Required account assurance has not been met")
    if inputs.eligibility_status not in {"passed", "not_required"}:
        reasons.append("Study eligibility has not been confirmed")
    if inputs.human_status == "blocked":
        reasons.append("Human-risk signals require review")

    if normalized_stage in {"registration", "start", "completion"} and reasons:
        return ParticipationRecommendation(BLOCK, tuple(reasons))

    if inputs.human_status == "review":
        reasons.append("Human-risk signals are inconclusive")
    if inputs.fraud_risk:
        reasons.append("Response quality checks found a fraud indicator")
    elif inputs.quality_status in {"low", "fraud_risk", "needs_review"}:
        reasons.append("Response quality requires review")
    if inputs.quality_score is not None and inputs.quality_score < 60:
        reasons.append("Response quality score is below the review threshold")

    if reasons:
        return ParticipationRecommendation(REVIEW, tuple(dict.fromkeys(reasons)))
    return ParticipationRecommendation(ALLOW, ())


def public_reason_codes(reasons: Iterable[str]) -> list[str]:
    """Return non-gameable reason categories safe for participant responses."""
    categories: list[str] = []
    for reason in reasons:
        normalized = reason.lower()
        if "assurance" in normalized:
            code = "verification_required"
        elif "eligibility" in normalized:
            code = "eligibility_required"
        elif "quality" in normalized or "fraud" in normalized:
            code = "response_review"
        else:
            code = "security_review"
        if code not in categories:
            categories.append(code)
    return categories
