"""Features / auth / navigation."""

from fastapi import Request
from typing import Optional
from urllib.parse import urlencode
from app.ai_growth.security import is_safe_internal_next
from app.models import User


MOBILE_USER_AGENT_TOKENS = ("mobile", "android", "iphone", "ipad")


def _is_mobile_request(request: Request) -> bool:
    user_agent = request.headers.get("user-agent", "").lower()
    return any(token in user_agent for token in MOBILE_USER_AGENT_TOKENS)


def _participant_dashboard_url(request: Request) -> str:
    return "/dashboard/mobile" if _is_mobile_request(request) else "/dashboard"


def _should_use_participant_app(request: Request, participant_app: Optional[str] = None) -> bool:
    if participant_app in {"0", "1"}:
        return participant_app == "1"
    return _is_mobile_request(request)


def _auth_uses_participant_app(request: Request, role: Optional[str] = None, participant_app: Optional[str] = None) -> bool:
    normalized_role = (role or "").strip().lower()
    if normalized_role == "participant":
        return True
    if normalized_role == "researcher":
        return False
    return _should_use_participant_app(request, participant_app)


def _post_auth_url(request: Request, participant_app: Optional[str] = None, welcome: bool = False) -> str:
    if _should_use_participant_app(request, participant_app):
        base_url = _participant_dashboard_url(request)
    else:
        base_url = "/publisher"
    return f"{base_url}?welcome=1" if welcome else base_url


def _post_auth_url_with_next(
    request: Request,
    participant_app: Optional[str] = None,
    next_url: Optional[str] = None,
    role: Optional[str] = None,
    welcome: bool = False
) -> str:
    if is_safe_internal_next(next_url):
        return next_url
    normalized_role = (role or "").strip().lower()
    if normalized_role == "participant":
        return _participant_dashboard_url(request)
    if normalized_role == "researcher":
        return "/publisher"
    return _post_auth_url(request, participant_app, welcome=welcome)


def _needs_identity_onboarding(user: User) -> bool:
    return not all([
        (getattr(user, "first_name", None) or "").strip(),
        (getattr(user, "last_name", None) or "").strip(),
        (getattr(user, "username", None) or "").strip(),
        (getattr(user, "birth_year", None) or "").strip(),
        (getattr(user, "birth_month", None) or "").strip(),
        (getattr(user, "education_level", None) or "").strip(),
        (getattr(user, "field", None) or "").strip(),
        (getattr(user, "status", None) or "").strip(),
        (getattr(user, "current_province", None) or getattr(user, "state", None) or "").strip(),
        (getattr(user, "language", None) or "").strip(),
        (getattr(user, "participation_format", None) or "").strip(),
    ])


def _identity_onboarding_url(return_to: Optional[str] = None, role: Optional[str] = None) -> str:
    safe_return = return_to if is_safe_internal_next(return_to) and return_to != "/complete-profile" else ""
    normalized_role = role if role in {"participant", "researcher"} else ""
    query = {}
    if safe_return:
        query["next"] = safe_return
    if normalized_role:
        query["role"] = normalized_role
    qs = urlencode(query) if query else ""
    return f"/complete-profile?{qs}" if qs else "/complete-profile"


def _post_auth_or_onboarding_url(
    user: User,
    final_url: str,
    role: Optional[str] = None,
    participant_app: Optional[str] = None,
) -> str:
    from app.features.messaging.account_messages import _welcome_email_role

    if _needs_identity_onboarding(user):
        return _identity_onboarding_url(
            final_url,
            _welcome_email_role(role, final_url, participant_app),
        )
    return final_url


__all__ = ['MOBILE_USER_AGENT_TOKENS', '_is_mobile_request', '_participant_dashboard_url', '_should_use_participant_app', '_auth_uses_participant_app', '_post_auth_url', '_post_auth_url_with_next', '_needs_identity_onboarding', '_identity_onboarding_url', '_post_auth_or_onboarding_url']
