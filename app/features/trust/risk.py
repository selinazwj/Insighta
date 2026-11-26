"""Features / trust / risk."""

from datetime import datetime
from datetime import timedelta
from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional
import hashlib
from app.models import QualityBlacklist
from app.models import Response
from app.models import ResponseQualityCheck
from app.models import RiskSignalEvent
from app.models import User
from app.models import Verification
from app.payouts import APPROVED
from app.payouts import LEGACY_RELEASED
from app.verification.assurance import HumanAssessment
from app.verification.assurance import HumanSignals
from app.verification.assurance import assess_human
from app.verification.assurance import evidence_reference


def _extract_client_meta(request: Request, current_user: User) -> dict:
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else None
    if not client_ip and request.client:
        client_ip = request.client.host
    user_agent = request.headers.get("user-agent")
    fingerprint = request.headers.get("x-device-fingerprint")
    fingerprint_confident = bool(fingerprint)
    if not fingerprint:
        # This fallback is intentionally coarse. It can contribute to a review
        # score, but is never treated as a stable identity or a sole block.
        raw = f"{user_agent or ''}|{getattr(current_user, 'device_type', '') or ''}"
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "device_fingerprint": fingerprint,
        "device_fingerprint_confident": fingerprint_confident,
    }


def _privacy_ref(kind: str, value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    return evidence_reference(f"risk_{kind}", normalized) if normalized else None


def _record_risk_signal(
    db: Session,
    request: Request,
    *,
    user: User,
    event_type: str,
    survey_id: Optional[int] = None,
    bot_challenge_passed: Optional[bool] = None,
    metadata: Optional[dict] = None,
) -> RiskSignalEvent:
    """Persist only HMAC references, never raw network or device identifiers."""
    meta = _extract_client_meta(request, user)
    row = RiskSignalEvent(
        user_id=user.id,
        survey_id=survey_id,
        event_type=event_type,
        network_ref=_privacy_ref("network", meta.get("client_ip")),
        device_ref=(
            _privacy_ref("device", meta.get("device_fingerprint"))
            if meta.get("device_fingerprint_confident") else None
        ),
        bot_challenge_passed=bot_challenge_passed,
        signal_metadata=metadata or {},
    )
    db.add(row)
    return row


def _human_assessment_for_request(
    db: Session,
    request: Request,
    user: User,
    *,
    event_type: str,
    survey_id: Optional[int] = None,
    record_event: bool = True,
):
    """Build a human-risk assessment from persistent, low-sensitivity signals."""
    now = datetime.utcnow()
    meta = _extract_client_meta(request, user)
    network_ref = _privacy_ref("network", meta.get("client_ip"))
    device_ref = (
        _privacy_ref("device", meta.get("device_fingerprint"))
        if meta.get("device_fingerprint_confident") else None
    )

    accounts_on_device = 1
    if device_ref:
        accounts_on_device = max(1, db.query(
            func.count(func.distinct(RiskSignalEvent.user_id))
        ).filter(
            RiskSignalEvent.device_ref == device_ref,
            RiskSignalEvent.user_id.isnot(None),
            RiskSignalEvent.created_at >= now - timedelta(days=90),
        ).scalar() or 0)

    registrations_from_ip_24h = 1
    if network_ref:
        registrations_from_ip_24h = max(1, db.query(RiskSignalEvent).filter(
            RiskSignalEvent.network_ref == network_ref,
            RiskSignalEvent.event_type == "registration",
            RiskSignalEvent.created_at >= now - timedelta(hours=24),
        ).count())

    accounts_on_network_for_study = 1
    study_actions_from_network_10m = 1
    if network_ref and survey_id:
        network_study_query = db.query(RiskSignalEvent).filter(
            RiskSignalEvent.network_ref == network_ref,
            RiskSignalEvent.survey_id == survey_id,
            RiskSignalEvent.event_type.in_(["start", "completion"]),
            RiskSignalEvent.created_at >= now - timedelta(days=90),
        )
        known_accounts = network_study_query.with_entities(
            func.count(func.distinct(RiskSignalEvent.user_id))
        ).scalar() or 0
        current_user_seen = network_study_query.filter(
            RiskSignalEvent.user_id == user.id,
        ).first() is not None
        accounts_on_network_for_study = max(
            1,
            int(known_accounts) + (0 if current_user_seen else 1),
        )
        study_actions_from_network_10m = max(
            1,
            network_study_query.filter(
                RiskSignalEvent.created_at >= now - timedelta(minutes=10),
            ).count() + 1,
        )

    rapid_actions = db.query(RiskSignalEvent).filter(
        RiskSignalEvent.user_id == user.id,
        RiskSignalEvent.created_at >= now - timedelta(minutes=5),
    ).count()
    prior_approved_responses = db.query(Response).filter(
        Response.participant_id == user.id,
        Response.payout_status.in_([APPROVED, LEGACY_RELEASED, "paid"]),
    ).count()
    prior_fraud_flags = db.query(ResponseQualityCheck).join(
        Response,
        Response.id == ResponseQualityCheck.response_id,
    ).filter(
        Response.participant_id == user.id,
        ResponseQualityCheck.fraud_risk.is_(True),
    ).count()
    prior_fraud_flags += db.query(QualityBlacklist).filter(
        QualityBlacklist.block_type == "user",
        QualityBlacklist.block_value == str(user.id),
    ).count()

    latest_registration = db.query(RiskSignalEvent).filter(
        RiskSignalEvent.user_id == user.id,
        RiskSignalEvent.event_type == "registration",
    ).order_by(RiskSignalEvent.created_at.desc()).first()
    email_control_proved = db.query(Verification).filter(
        Verification.user_id == user.id,
        Verification.status == "verified",
        Verification.method.in_(["email_otp", "institutional_email"]),
    ).first() is not None or bool(
        getattr(user, "verification_status", "") == "verified"
        and getattr(user, "verified_email", None)
    )
    # Accounts created before risk telemetry existed have no historical
    # challenge event. Treat proven email control as a neutral migration
    # baseline rather than penalizing every legacy participant.
    bot_challenge_passed = (
        bool(latest_registration.bot_challenge_passed)
        if latest_registration else email_control_proved
    )

    assessment = assess_human(HumanSignals(
        bot_challenge_passed=bot_challenge_passed,
        email_control_proved=email_control_proved,
        accounts_on_device=accounts_on_device,
        registrations_from_ip_24h=registrations_from_ip_24h,
        accounts_on_network_for_study=accounts_on_network_for_study,
        study_actions_from_network_10m=study_actions_from_network_10m,
        rapid_actions=rapid_actions,
        prior_approved_responses=prior_approved_responses,
        prior_fraud_flags=prior_fraud_flags,
    ))
    # A manual approval is a narrow, time-limited override of the automated
    # human-risk status. It never bypasses study eligibility, required email
    # domains, or other account-assurance requirements.
    manual_approval = db.query(Verification).filter(
        Verification.user_id == user.id,
        Verification.method == "admin_review",
        Verification.attribute == "account_trust_review",
        Verification.status == "verified",
        Verification.expires_at.isnot(None),
        Verification.expires_at > now,
    ).order_by(Verification.verified_at.desc()).first()
    if manual_approval:
        assessment = HumanAssessment(
            status="likely_human",
            risk_score=assessment.risk_score,
            reasons=tuple(dict.fromkeys([
                *assessment.reasons,
                "Human-risk status was approved by an administrator",
            ])),
        )
    if record_event:
        _record_risk_signal(
            db,
            request,
            user=user,
            event_type=event_type,
            survey_id=survey_id,
            metadata={
                "human_status": assessment.status,
                "risk_score": assessment.risk_score,
            },
        )
    return assessment


__all__ = ['_extract_client_meta', '_privacy_ref', '_record_risk_signal', '_human_assessment_for_request']
