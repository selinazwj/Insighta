"""Verification assurance primitives.

The platform keeps three separate ideas:

* evidence: what control or attribute a participant has demonstrated;
* assurance: the strongest reusable account-level conclusion from that evidence;
* eligibility: whether the participant fits one particular study.

Eligibility and response quality deliberately do not live in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
from typing import Iterable, Optional


TIER_TRUSTED = "tier_2"
TIER_BASIC = "tier_3"

TIER_RANK = {
    TIER_TRUSTED: 2,
    TIER_BASIC: 3,
}

TIER_LABELS = {
    TIER_TRUSTED: "Account and professional checks complete",
    TIER_BASIC: "Account check complete",
}

# A method is evidence, not a tier by itself. These sets describe the strongest
# assurance that a currently supported method may contribute to.
BASIC_METHODS = frozenset({
    "email_otp",
    "google_oauth",
    "linkedin_oauth",
})
TRUSTED_METHODS = frozenset({
    "institutional_email",
    "linkedin_oauth",
    "professional_registry",
    "trusted_history",
    "didit",  # Didit identity verification (document / biometric session)
})
def tier_rank(tier: Optional[str]) -> int:
    return TIER_RANK.get(tier or "", 99)


def tier_satisfies(actual: Optional[str], required: Optional[str]) -> bool:
    return tier_rank(actual) <= TIER_RANK.get(required or TIER_BASIC, 3)


def strongest_tier_for_methods(methods: Iterable[str]) -> Optional[str]:
    normalized = {str(method or "").strip().lower() for method in methods}
    if normalized & TRUSTED_METHODS:
        return TIER_TRUSTED
    if normalized & BASIC_METHODS:
        return TIER_BASIC
    return None


def evidence_reference(method: str, subject: str) -> str:
    """Return a stable, non-reversible reference for uniqueness checks."""
    secret = (
        os.environ.get("VERIFICATION_EVIDENCE_SECRET")
        or os.environ.get("SECRET_KEY")
    )
    environment = (
        os.environ.get("ENVIRONMENT")
        or os.environ.get("VERCEL_ENV")
        or ""
    ).strip().lower()
    if not secret and environment in {"production", "prod"}:
        raise RuntimeError("VERIFICATION_EVIDENCE_SECRET is required in production")
    secret = secret or "insighta-verification-local-development-only"
    normalized = f"{method.strip().lower()}:{subject.strip().lower()}"
    return hmac.new(
        secret.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class HumanSignals:
    """Low-sensitivity signals used to decide whether to step verification up."""

    bot_challenge_passed: bool
    email_control_proved: bool
    accounts_on_device: int = 1
    registrations_from_ip_24h: int = 1
    accounts_on_network_for_study: int = 1
    study_actions_from_network_10m: int = 1
    rapid_actions: int = 0
    prior_approved_responses: int = 0
    prior_fraud_flags: int = 0


@dataclass(frozen=True)
class HumanAssessment:
    status: str  # likely_human | review | blocked
    risk_score: int
    reasons: tuple[str, ...]


def assess_human(signals: HumanSignals) -> HumanAssessment:
    """Combine several weak signals; no single CAPTCHA is treated as proof."""
    risk = 0
    reasons: list[str] = []

    if not signals.bot_challenge_passed:
        risk += 60
        reasons.append("Bot challenge was not validated")
    if not signals.email_control_proved:
        risk += 35
        reasons.append("Email control has not been proved")
    if signals.accounts_on_device > 2:
        risk += min(35, (signals.accounts_on_device - 2) * 10)
        reasons.append("Several accounts are associated with this device")
    network_risk = 0
    if signals.registrations_from_ip_24h > 5:
        network_risk += min(30, (signals.registrations_from_ip_24h - 5) * 4)
        reasons.append("Unusual registration velocity from this network")
    if signals.accounts_on_network_for_study > 2:
        network_risk += min(
            45,
            (signals.accounts_on_network_for_study - 2) * 15,
        )
        reasons.append("Several accounts on this network joined the same study")
    if signals.study_actions_from_network_10m > 8:
        network_risk += min(
            30,
            (signals.study_actions_from_network_10m - 8) * 5,
        )
        reasons.append("Unusual study activity from this network")
    # Shared networks are common in homes, campuses, and workplaces. Network
    # evidence alone may trigger review, but cannot cross the block threshold.
    risk += min(45, network_risk)
    if signals.rapid_actions > 3:
        risk += min(25, (signals.rapid_actions - 3) * 5)
        reasons.append("Interaction speed is unusually high")
    if signals.prior_fraud_flags:
        risk += min(60, signals.prior_fraud_flags * 25)
        reasons.append("The account has prior fraud flags")
    if signals.prior_approved_responses >= 3:
        risk = max(0, risk - 15)

    risk = min(100, max(0, risk))
    if risk >= 70:
        status = "blocked"
    elif risk >= 35:
        status = "review"
    else:
        status = "likely_human"
    return HumanAssessment(status=status, risk_score=risk, reasons=tuple(reasons))
