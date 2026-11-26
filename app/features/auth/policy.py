"""Features / auth / policy."""

from fastapi import Request
from sqlalchemy.orm import Session
from typing import Optional
import os
from app.models import QualityBlacklist


def _local_dev_email_codes_enabled(request: Request) -> bool:
    if os.environ.get("LOCAL_DEV_EMAIL_CODES") == "1":
        return True
    host = (request.url.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "0.0.0.0"}


def _normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


DEFAULT_BLOCKED_EMAILS = {
    "edward.websterjr@gmail.com",
}


def _configured_blocked_emails() -> set[str]:
    configured = os.environ.get("BLOCKED_EMAILS") or ""
    return {
        _normalize_email(email)
        for email in configured.split(",")
        if _normalize_email(email)
    }


def _is_email_blocked(db: Session, email: Optional[str]) -> bool:
    normalized = _normalize_email(email)
    if not normalized:
        return False
    if normalized in DEFAULT_BLOCKED_EMAILS or normalized in _configured_blocked_emails():
        return True
    try:
        return db.query(QualityBlacklist).filter(
            QualityBlacklist.block_type == "email",
            QualityBlacklist.block_value == normalized,
        ).first() is not None
    except Exception:
        return False


def _blocked_auth_message() -> str:
    return "This account is not available. Please contact Insighta support if you believe this is a mistake."


__all__ = ['_local_dev_email_codes_enabled', '_normalize_email', 'DEFAULT_BLOCKED_EMAILS', '_configured_blocked_emails', '_is_email_blocked', '_blocked_auth_message']
