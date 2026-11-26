"""Features / admin / access."""

from sqlalchemy import func
from sqlalchemy.orm import Session
import hmac
import os
from app.models import User


def _admin_key_matches(value: str | None) -> bool:
    expected = (os.environ.get("ADMIN_KEY") or "").strip()
    provided = (value or "").strip()
    return bool(expected and provided) and hmac.compare_digest(provided, expected)


def _get_admin_publisher_user(db: Session) -> User:
    from app.features.auth.referrals import _ensure_user_referral_code
    from app.features.auth.referrals import _generate_referral_code

    email = (os.environ.get("ADMIN_PUBLISHER_EMAIL") or "vfsa@bu.edu").strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user:
        _ensure_user_referral_code(db, user, commit=True)
        return user
    user = User(
        email=email,
        password="admin-managed-publisher",
        username="Insighta Admin",
        status="Researcher",
        referral_code=_generate_referral_code(db),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


__all__ = ['_admin_key_matches', '_get_admin_publisher_user']
