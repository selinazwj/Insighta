"""Features / verification / service."""

from datetime import datetime
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional
import re
from app.bootstrap.runtime import templates
from app.bootstrap.settings import LINKEDIN_CLIENT_ID
from app.models import DiditSession
from app.models import Survey
from app.models import User
from app.models import Verification
from app.verification.assurance import TIER_BASIC
from app.verification.assurance import TIER_IDENTITY
from app.verification.assurance import TIER_LABELS
from app.verification.assurance import TIER_RANK
from app.verification.assurance import TIER_TRUSTED
from app.verification.assurance import evidence_reference
from app.verification.assurance import strongest_tier_for_methods
from app.verification.assurance import tier_rank
from app.verification.assurance import tier_satisfies
from app.verification.didit_client import didit_configured


def _tier_rank(tier: Optional[str]) -> int:
    return tier_rank(tier)


def _tier_satisfies(user_tier: Optional[str], required_tier: Optional[str]) -> bool:
    return tier_satisfies(user_tier, required_tier)


def _active_verification_methods(db: Session, user_id: int) -> set[str]:
    now = datetime.utcnow()
    rows = db.query(Verification).filter(
        Verification.user_id == user_id,
        Verification.status == "verified",
        or_(Verification.expires_at.is_(None), Verification.expires_at > now),
    ).all()
    return {(row.method or "").strip().lower() for row in rows if row.method}


def _current_assurance_tier(db: Session, user: User) -> Optional[str]:
    """
    Derive assurance from active evidence instead of trusting one mutable tier.

    Legacy email/OAuth fields remain a temporary fallback for existing accounts,
    but a legacy ID-upload tier is intentionally not accepted as high assurance.
    """
    methods = _active_verification_methods(db, user.id)
    tier = strongest_tier_for_methods(methods)
    if tier:
        return tier
    if getattr(user, "oauth_provider", None) == "linkedin" and getattr(user, "oauth_id", None):
        return TIER_TRUSTED
    if (
        getattr(user, "verification_status", "unverified") == "verified"
        and (getattr(user, "verified_email", None) or getattr(user, "email", None))
    ):
        return TIER_BASIC
    return None


def _verified_evidence_owner(
    db: Session,
    *,
    method: str,
    subject: str,
    excluding_user_id: Optional[int] = None,
) -> Optional[int]:
    ref = evidence_reference(method, subject)
    query = db.query(Verification).filter(
        Verification.method == method,
        Verification.evidence_ref == ref,
        Verification.status == "verified",
    )
    if excluding_user_id is not None:
        query = query.filter(Verification.user_id != excluding_user_id)
    row = query.first()
    return row.user_id if row else None


def _record_verified_evidence(
    db: Session,
    *,
    user_id: int,
    attribute: str,
    method: str,
    subject: str,
    trust_score: float,
    valid_days: int = 365,
) -> Verification:
    ref = evidence_reference(method, subject)
    existing = db.query(Verification).filter(
        Verification.user_id == user_id,
        Verification.method == method,
        Verification.evidence_ref == ref,
    ).first()
    now = datetime.utcnow()
    row = existing or Verification(user_id=user_id, method=method, evidence_ref=ref)
    row.attribute = attribute
    row.status = "verified"
    row.trust_score = trust_score
    row.verified_at = now
    row.expires_at = now + timedelta(days=valid_days)
    row.verified_by = "system"
    if not existing:
        db.add(row)
    return row


def _latest_didit_session(db: Session, user_id: int) -> Optional[DiditSession]:
    return (
        db.query(DiditSession)
        .filter(DiditSession.user_id == user_id)
        .order_by(DiditSession.updated_at.desc(), DiditSession.id.desc())
        .first()
    )


def _verification_status_payload(db: Session, user: User, session_id: Optional[str] = None) -> dict:
    methods = sorted(_active_verification_methods(db, user.id))
    tier = _current_assurance_tier(db, user)
    didit_row = _latest_didit_session(db, user.id)
    if session_id:
        didit_row = db.query(DiditSession).filter(
            DiditSession.session_id == session_id, DiditSession.user_id == user.id,
        ).first()
        if not didit_row:
            raise HTTPException(404, "Verification session not found")
    identity_status = didit_row.status if didit_row else "unverified"
    if "didit" in methods:
        identity_status = "verified"
    elif identity_status == "verified":
        identity_status = "expired"
    # Account-level status prefers reusable assurance; Didit pending/rejected is still exposed under didit.
    if tier:
        status = "verified"
    elif didit_row:
        status = didit_row.status
    else:
        status = getattr(user, "verification_status", None) or "unverified"
    payload = {
        "status": status,
        "tier": tier,
        "methods": methods,
        "didit_configured": didit_configured(),
        "identity_status": identity_status,
        "updated_at": None,
        "didit": None,
    }
    if didit_row:
        payload["updated_at"] = (
            didit_row.updated_at.isoformat() + "Z" if didit_row.updated_at else None
        )
        payload["didit"] = {
            "session_id": didit_row.session_id,
            "provider_status": didit_row.provider_status,
            "status": "expired" if didit_row.status == "verified" and "didit" not in methods else didit_row.status,
            "workflow_id": didit_row.workflow_id,
            "updated_at": payload["updated_at"],
        }
    elif getattr(user, "verified_at", None):
        payload["updated_at"] = user.verified_at.isoformat() + "Z"
    return payload


def _email_domain(email: Optional[str]) -> str:
    """pull the domain part of an email, lowercased. 'A@BU.edu' -> 'bu.edu'"""
    if not email or "@" not in email:
        return ""
    return email.strip().lower().rsplit("@", 1)[-1]


def _normalize_domains(raw: Optional[str]) -> list:
    """
    turn a researchers free-text box into a clean list of domains.
    accepts commas/spaces and a leading @, so '@BU.edu, gmail.com' -> ['bu.edu','gmail.com']
    """
    if not raw:
        return []
    out = []
    for part in re.split(r"[,\s]+", raw.strip()):
        part = part.strip().lower().lstrip("@")
        if part and part not in out:
            out.append(part)
    return out


def _verified_email_for(user: User) -> str:
    """the email a user has proven — the one they OTP'd, else their account email"""
    return getattr(user, "verified_email", None) or getattr(user, "email", "") or ""


def _domains_pretty(domains: list) -> str:
    """['bu.edu','gmail.com'] -> '@bu.edu or @gmail.com' for friendly messages"""
    tagged = ["@" + d for d in domains]
    if len(tagged) <= 1:
        return tagged[0] if tagged else ""
    return ", ".join(tagged[:-1]) + " or " + tagged[-1]


def _verification_satisfies(user: User, survey: Survey, db: Session) -> bool:
    """
    single source of truth: does this user satisfy this surveys verification rule?
    checks tier AND (if set) that the users proven email is at an allowed domain.
    """
    required_tier = getattr(survey, "required_verification_tier", None) or "tier_3"
    actual_tier = _current_assurance_tier(db, user)
    if not _tier_satisfies(actual_tier, required_tier):
        return False
    domains = _normalize_domains(getattr(survey, "required_email_domains", None))
    if domains and _email_domain(_verified_email_for(user)) not in domains:
        return False
    return True


def _render_verify(request, current_user, db, *, error=None, notice=None, needs_domains=None, prefill_email=None, focus_tier=None, done=False):
    # ive added this helper so the GET page and every error rerender share one context
    from app.web.middleware import no_store_response

    assurance_tier = _current_assurance_tier(db, current_user)
    is_verified = assurance_tier is not None
    user_rank = _tier_rank(assurance_tier) if is_verified else 99
    domains = _normalize_domains(needs_domains)
    # show the email-verify method to anyone unverified, OR to a verified user who
    # still needs to prove a specific domain (e.g. verified via gmail but study needs bu.edu)
    show_email_verify = (not is_verified) or bool(domains)
    prefill = prefill_email or getattr(current_user, "verified_email", None) or current_user.email
    # Didit is a separate identity requirement; professional/email evidence
    # cannot replace it. Email-domain and screening requirements still apply.
    focus_tier = focus_tier if focus_tier in TIER_RANK else None
    return no_store_response(templates.TemplateResponse("verify.html", {
        "request": request, "current_user": current_user, "error": error, "notice": notice,
        "needs_domains": ", ".join(domains) if domains else None,
        "prefill_email": prefill,
        "show_email_verify": show_email_verify,
        "focus_tier": focus_tier,
        "needs_identity_check": focus_tier == TIER_IDENTITY and not _tier_satisfies(assurance_tier, TIER_IDENTITY),
        "assurance_tier": assurance_tier,
        "assurance_label": TIER_LABELS.get(assurance_tier, "Not verified"),
        # ive added this — right after a successful verification we show a clean
        # "youre done" screen instead of advertising the other tiers again
        "done": bool(done),
        "linkedin_enabled": bool(LINKEDIN_CLIENT_ID),
        # Verified users can still add stronger low-sensitivity evidence.
        "can_upgrade_linkedin": bool(LINKEDIN_CLIENT_ID) and user_rank > 2 and focus_tier != TIER_IDENTITY,
    }))


__all__ = ['_tier_rank', '_tier_satisfies', '_active_verification_methods', '_current_assurance_tier', '_verified_evidence_owner', '_record_verified_evidence', '_latest_didit_session', '_verification_status_payload', '_email_domain', '_normalize_domains', '_verified_email_for', '_domains_pretty', '_verification_satisfies', '_render_verify']
