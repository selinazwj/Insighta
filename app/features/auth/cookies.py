"""Features / auth / cookies."""

from fastapi import Request
from fastapi.responses import RedirectResponse
from typing import Optional
from app.ai_growth.security import is_safe_internal_next
from app.auth_session import sign_session
from app.models import Response
from app.models import User


def _cookie_policy(request: Optional[Request]) -> dict:
    is_https = False
    if request is not None:
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        is_https = (request.url.scheme == "https") or (forwarded_proto == "https")
    if is_https:
        return {"httponly": True, "samesite": "lax", "secure": True}
    return {"httponly": True, "samesite": "lax", "secure": False}


AUTH_RETURN_COOKIE = "auth_return_to"


AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def _set_user_cookie(response, request: Request, user: User) -> None:
    response.set_cookie(
        "user_id",
        sign_session(user.id),
        max_age=AUTH_COOKIE_MAX_AGE,
        **_cookie_policy(request),
    )


def _safe_auth_return(*candidates: Optional[str]) -> str:
    for candidate in candidates:
        if is_safe_internal_next(candidate):
            return candidate
    return ""


def _remember_auth_return(response: Response, request: Request, next_url: Optional[str]) -> None:
    safe_next = _safe_auth_return(next_url)
    if safe_next:
        response.set_cookie(
            AUTH_RETURN_COOKIE,
            safe_next,
            max_age=30 * 60,
            **_cookie_policy(request),
        )


def _clear_auth_return(response: Response, request: Request) -> None:
    response.delete_cookie(AUTH_RETURN_COOKIE, **_cookie_policy(request))


def _mark_participant_app(response: RedirectResponse, request: Optional[Request] = None):
    response.set_cookie(
        "participant_app",
        "1",
        max_age=60 * 60 * 24 * 30,
        **_cookie_policy(request),
    )


def _clear_participant_app(response, request: Optional[Request] = None):
    # Keep an explicit researcher preference so a phone does not change roles.
    response.set_cookie("participant_app", "0", max_age=AUTH_COOKIE_MAX_AGE, **_cookie_policy(request))


__all__ = ['_cookie_policy', 'AUTH_RETURN_COOKIE', 'AUTH_COOKIE_MAX_AGE', '_set_user_cookie', '_safe_auth_return', '_remember_auth_return', '_clear_auth_return', '_mark_participant_app', '_clear_participant_app']
