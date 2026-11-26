"""Features / auth / referrals."""

from fastapi import Request
from sqlalchemy.orm import Session
from typing import Optional
from urllib.parse import urlencode
import re
import secrets
from app.bootstrap.settings import BASE_URL
from app.models import User


PENDING_REFERRAL_COOKIE = "pending_referral"


_REFERRAL_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _normalize_referral_code(value: Optional[str]) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (value or "").strip()).upper()


def _generate_referral_code(db: Session) -> str:
    for _ in range(40):
        code = "".join(secrets.choice(_REFERRAL_CODE_ALPHABET) for _ in range(8))
        exists = db.query(User.id).filter(User.referral_code == code).first()
        if not exists:
            return code
    return secrets.token_hex(5).upper()


def _ensure_user_referral_code(db: Session, user: User, *, commit: bool = False) -> str:
    if user.referral_code:
        return user.referral_code
    user.referral_code = _generate_referral_code(db)
    db.add(user)
    if commit:
        db.commit()
        db.refresh(user)
    return user.referral_code


def _find_referrer_by_code(db: Session, code: Optional[str]) -> Optional[User]:
    normalized = _normalize_referral_code(code)
    if not normalized:
        return None
    return db.query(User).filter(User.referral_code == normalized).first()


def _apply_referral_code(db: Session, user: User, code: Optional[str]) -> Optional[str]:
    """Attach invited_by if code is valid. Returns error message or None."""
    normalized = _normalize_referral_code(code)
    if not normalized:
        return None
    referrer = _find_referrer_by_code(db, normalized)
    if not referrer:
        return "Referral code not found."
    if user.id and referrer.id == user.id:
        return "You cannot use your own referral code."
    if not user.invited_by_user_id:
        user.invited_by_user_id = referrer.id
    return None


def _referral_invite_url(code: str, role: Optional[str] = None) -> str:
    params = {"ref": code}
    if role in {"participant", "researcher"}:
        params["role"] = role
    return f"{BASE_URL.rstrip('/')}/register?{urlencode(params)}"


def _remember_pending_referral(response, request: Request, code: Optional[str]) -> None:
    from app.features.auth.cookies import _cookie_policy

    normalized = _normalize_referral_code(code)
    if not normalized:
        return
    response.set_cookie(
        PENDING_REFERRAL_COOKIE,
        normalized,
        max_age=60 * 60 * 24 * 14,
        **_cookie_policy(request),
    )


def _clear_pending_referral(response, request: Optional[Request] = None) -> None:
    from app.features.auth.cookies import _cookie_policy

    response.delete_cookie(PENDING_REFERRAL_COOKIE, **_cookie_policy(request))


__all__ = ['PENDING_REFERRAL_COOKIE', '_REFERRAL_CODE_ALPHABET', '_normalize_referral_code', '_generate_referral_code', '_ensure_user_referral_code', '_find_referrer_by_code', '_apply_referral_code', '_referral_invite_url', '_remember_pending_referral', '_clear_pending_referral']
