"""Features / auth / codes."""

from datetime import datetime
from datetime import timedelta
from sqlalchemy.orm import Session
from typing import Optional
import random
import re
from app.bootstrap.settings import VERIFICATION_CODE_EXPIRE_MINUTES
from app.models import EmailVerificationCode


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked_name = name[0] + "*" * max(len(name) - 1, 0)
    else:
        masked_name = name[:2] + "*" * max(len(name) - 2, 0)
    return f"{masked_name}@{domain}"


def _generate_verification_code() -> str:
    return f"{random.randint(0, 999999):06d}"


PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 8 characters and include an uppercase letter, "
    "a lowercase letter, a number, and a special character."
)


def _validate_registration_password(password: str) -> Optional[str]:
    value = password or ""
    if len(value) < 8: return PASSWORD_POLICY_MESSAGE
    if not re.search(r"[A-Z]", value): return PASSWORD_POLICY_MESSAGE
    if not re.search(r"[a-z]", value): return PASSWORD_POLICY_MESSAGE
    if not re.search(r"\d", value): return PASSWORD_POLICY_MESSAGE
    if not re.search(r"[^A-Za-z0-9]", value): return PASSWORD_POLICY_MESSAGE
    return None


def _mark_previous_codes_used(db: Session, email: str, purpose: str):
    pending_codes = db.query(EmailVerificationCode).filter(
        EmailVerificationCode.email == email,
        EmailVerificationCode.purpose == purpose,
        EmailVerificationCode.used_at.is_(None)
    ).all()
    now = datetime.utcnow()
    for item in pending_codes:
        item.used_at = now


def _latest_active_verification_code(db: Session, email: str, purpose: str) -> Optional[EmailVerificationCode]:
    now = datetime.utcnow()
    return db.query(EmailVerificationCode).filter(
        EmailVerificationCode.email == email,
        EmailVerificationCode.purpose == purpose,
        EmailVerificationCode.used_at.is_(None),
        EmailVerificationCode.expires_at > now,
    ).order_by(EmailVerificationCode.created_at.desc()).first()


def _issue_verification_code(db: Session, email: str, purpose: str) -> tuple[str, bool]:
    existing = _latest_active_verification_code(db, email, purpose)
    if existing:
        return existing.code, False
    code = _generate_verification_code()
    db.add(EmailVerificationCode(
        email=email,
        purpose=purpose,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=VERIFICATION_CODE_EXPIRE_MINUTES)
    ))
    db.commit()
    return code, True


def _consume_verification_code(db: Session, email: str, purpose: str, code: str) -> bool:
    from app.features.auth.policy import _normalize_email

    normalized_email = _normalize_email(email)
    normalized_code = (code or "").strip()
    record = db.query(EmailVerificationCode).filter(
        EmailVerificationCode.email == normalized_email,
        EmailVerificationCode.purpose == purpose,
        EmailVerificationCode.code == normalized_code,
        EmailVerificationCode.used_at.is_(None)
    ).order_by(EmailVerificationCode.created_at.desc()).first()
    if not record:
        return False
    if record.expires_at < datetime.utcnow():
        return False
    _mark_previous_codes_used(db, normalized_email, purpose)
    db.commit()
    return True


__all__ = ['_mask_email', '_generate_verification_code', 'PASSWORD_POLICY_MESSAGE', '_validate_registration_password', '_mark_previous_codes_used', '_latest_active_verification_code', '_issue_verification_code', '_consume_verification_code']
