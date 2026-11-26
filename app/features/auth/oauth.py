"""Features / auth / oauth."""

from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import Optional
from urllib.parse import urlencode
import httpx
import secrets
from app.bootstrap.runtime import pwd_context
from app.bootstrap.settings import BASE_URL
from app.bootstrap.settings import GOOGLE_AUTH_URL
from app.bootstrap.settings import GOOGLE_CLIENT_ID
from app.bootstrap.settings import GOOGLE_CLIENT_SECRET
from app.bootstrap.settings import GOOGLE_TOKEN_URL
from app.bootstrap.settings import GOOGLE_USERINFO_URL
from app.bootstrap.settings import LINKEDIN_AUTH_URL
from app.bootstrap.settings import LINKEDIN_CLIENT_ID
from app.bootstrap.settings import LINKEDIN_CLIENT_SECRET
from app.bootstrap.settings import LINKEDIN_TOKEN_URL
from app.bootstrap.settings import LINKEDIN_USERINFO_URL
from app.database import get_db
from app.features.auth.cookies import AUTH_RETURN_COOKIE
from app.features.auth.referrals import PENDING_REFERRAL_COOKIE
from app.models import User
from app.verification.assurance import TIER_BASIC
from app.verification.assurance import TIER_TRUSTED


def google_login(request: Request):
    from app.features.auth.cookies import _cookie_policy

    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google login is not configured.")
    state = secrets.token_urlsafe(16)
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    })
    response = RedirectResponse(f"{GOOGLE_AUTH_URL}?{params}", status_code=302)
    response.set_cookie("oauth_state", state, max_age=300, **_cookie_policy(request))
    return response


async def google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from app.features.auth.cookies import _clear_auth_return
    from app.features.auth.cookies import _cookie_policy
    from app.features.auth.cookies import _safe_auth_return
    from app.features.auth.cookies import _set_user_cookie
    from app.features.auth.navigation import _post_auth_or_onboarding_url
    from app.features.auth.navigation import _post_auth_url_with_next
    from app.features.auth.policy import _blocked_auth_message
    from app.features.auth.policy import _is_email_blocked
    from app.features.auth.policy import _normalize_email
    from app.features.auth.referrals import _apply_referral_code
    from app.features.auth.referrals import _clear_pending_referral
    from app.features.auth.referrals import _ensure_user_referral_code
    from app.features.auth.referrals import _generate_referral_code
    from app.features.verification.service import _current_assurance_tier
    from app.features.verification.service import _record_verified_evidence
    from app.features.verification.service import _tier_rank
    from app.features.verification.service import _verified_evidence_owner

    if error:
        return RedirectResponse("/login?oauth_error=Google+login+was+cancelled.", status_code=302)

    stored_state = request.cookies.get("oauth_state")
    if not state or state != stored_state:
        return RedirectResponse("/login?oauth_error=Invalid+state.+Please+try+again.", status_code=302)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{BASE_URL}/auth/google/callback",
            "grant_type": "authorization_code",
        })
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return RedirectResponse("/login?oauth_error=Google+token+exchange+failed.", status_code=302)

        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo = userinfo_resp.json()

    email = _normalize_email(userinfo.get("email", ""))
    google_id = str(userinfo.get("id", ""))
    name = userinfo.get("name") or userinfo.get("given_name")

    if not email or not google_id:
        return RedirectResponse("/login?oauth_error=Could+not+retrieve+email+from+Google.", status_code=302)
    if _is_email_blocked(db, email):
        return RedirectResponse(f"/login?oauth_error={urlencode({'': _blocked_auth_message()})[1:]}", status_code=302)

    user = db.query(User).filter(User.email == email).first()
    evidence_owner = _verified_evidence_owner(
        db,
        method="google_oauth",
        subject=google_id,
    )
    if evidence_owner and (not user or evidence_owner != user.id):
        return RedirectResponse(
            "/login?oauth_error=That+Google+account+is+already+linked+to+another+participant.",
            status_code=302,
        )
    is_new = False
    if not user:
        user = User(
            email=email,
            password=pwd_context.hash(secrets.token_urlsafe(32)),
            username=name,
            oauth_provider="google",
            oauth_id=google_id,
            referral_code=_generate_referral_code(db),
        )
        pending_ref = request.cookies.get(PENDING_REFERRAL_COOKIE)
        _apply_referral_code(db, user, pending_ref)
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new = True
    else:
        if not user.oauth_provider:
            user.oauth_provider = "google"
            user.oauth_id = google_id
        _ensure_user_referral_code(db, user, commit=True)
    existing_tier = _current_assurance_tier(db, user)
    _record_verified_evidence(
        db,
        user_id=user.id,
        attribute="email_account_control",
        method="google_oauth",
        subject=google_id,
        trust_score=0.45,
    )
    user.verification_status = "verified"
    user.verified_email = email
    user.verified_tier = existing_tier if _tier_rank(existing_tier) < _tier_rank(TIER_BASIC) else TIER_BASIC
    user.verified_at = datetime.utcnow()
    db.commit()

    return_to = _safe_auth_return(request.cookies.get(AUTH_RETURN_COOKIE))
    final_url = _post_auth_url_with_next(
        request,
        request.cookies.get("participant_app"),
        return_to,
        welcome=is_new,
    )
    redirect_url = _post_auth_or_onboarding_url(
        user,
        final_url,
        participant_app=request.cookies.get("participant_app"),
    )
    resp = RedirectResponse(redirect_url, status_code=303)
    policy = _cookie_policy(request)
    _set_user_cookie(resp, request, user)
    resp.delete_cookie("oauth_state", samesite=policy["samesite"], secure=policy["secure"])
    _clear_auth_return(resp, request)
    if is_new:
        _clear_pending_referral(resp, request)
    return resp


def linkedin_login(request: Request):
    from app.features.auth.cookies import _cookie_policy
    from app.features.auth.oauth_settings import _linkedin_redirect_uri

    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(503, "LinkedIn login is not configured.")
    state = secrets.token_urlsafe(16)
    params = urlencode({
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": _linkedin_redirect_uri("/auth/linkedin/callback"),
        "state": state,
        "scope": "openid profile email",
    })
    response = RedirectResponse(f"{LINKEDIN_AUTH_URL}?{params}", status_code=302)
    response.set_cookie("oauth_state", state, max_age=300, **_cookie_policy(request))
    return response


async def linkedin_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from app.features.auth.cookies import _clear_auth_return
    from app.features.auth.cookies import _cookie_policy
    from app.features.auth.cookies import _safe_auth_return
    from app.features.auth.cookies import _set_user_cookie
    from app.features.auth.navigation import _post_auth_or_onboarding_url
    from app.features.auth.navigation import _post_auth_url_with_next
    from app.features.auth.oauth_settings import _linkedin_redirect_uri
    from app.features.auth.policy import _blocked_auth_message
    from app.features.auth.policy import _is_email_blocked
    from app.features.auth.policy import _normalize_email
    from app.features.auth.referrals import _apply_referral_code
    from app.features.auth.referrals import _clear_pending_referral
    from app.features.auth.referrals import _ensure_user_referral_code
    from app.features.auth.referrals import _generate_referral_code
    from app.features.verification.service import _current_assurance_tier
    from app.features.verification.service import _record_verified_evidence
    from app.features.verification.service import _tier_rank
    from app.features.verification.service import _verified_evidence_owner

    if error:
        return RedirectResponse("/login?oauth_error=LinkedIn+login+was+cancelled.", status_code=302)

    stored_state = request.cookies.get("oauth_state")
    if not state or state != stored_state:
        return RedirectResponse("/login?oauth_error=Invalid+state.+Please+try+again.", status_code=302)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _linkedin_redirect_uri("/auth/linkedin/callback"),
                "client_id": LINKEDIN_CLIENT_ID,
                "client_secret": LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return RedirectResponse("/login?oauth_error=LinkedIn+token+exchange+failed.", status_code=302)

        userinfo_resp = await client.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo = userinfo_resp.json()

    email = _normalize_email(userinfo.get("email", ""))
    linkedin_id = str(userinfo.get("sub", ""))
    name = userinfo.get("name") or userinfo.get("given_name")

    if not email or not linkedin_id:
        return RedirectResponse("/login?oauth_error=Could+not+retrieve+email+from+LinkedIn.", status_code=302)
    if _is_email_blocked(db, email):
        return RedirectResponse(f"/login?oauth_error={urlencode({'': _blocked_auth_message()})[1:]}", status_code=302)

    user = db.query(User).filter(User.email == email).first()
    evidence_owner = _verified_evidence_owner(
        db,
        method="linkedin_oauth",
        subject=linkedin_id,
    )
    if evidence_owner and (not user or evidence_owner != user.id):
        return RedirectResponse(
            "/login?oauth_error=That+LinkedIn+account+is+already+linked+to+another+participant.",
            status_code=302,
        )
    is_new = False
    if not user:
        user = User(
            email=email,
            password=pwd_context.hash(secrets.token_urlsafe(32)),
            username=name,
            oauth_provider="linkedin",
            oauth_id=linkedin_id,
            referral_code=_generate_referral_code(db),
        )
        pending_ref = request.cookies.get(PENDING_REFERRAL_COOKIE)
        _apply_referral_code(db, user, pending_ref)
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new = True
    else:
        if not user.oauth_provider:
            user.oauth_provider = "linkedin"
            user.oauth_id = linkedin_id
        _ensure_user_referral_code(db, user, commit=True)
    existing_tier = _current_assurance_tier(db, user)
    _record_verified_evidence(
        db,
        user_id=user.id,
        attribute="professional_account_control",
        method="linkedin_oauth",
        subject=linkedin_id,
        trust_score=0.65,
    )
    user.verification_status = "verified"
    user.verified_email = email
    user.verified_tier = existing_tier if _tier_rank(existing_tier) < _tier_rank(TIER_TRUSTED) else TIER_TRUSTED
    user.verified_at = datetime.utcnow()
    db.commit()

    return_to = _safe_auth_return(request.cookies.get(AUTH_RETURN_COOKIE))
    final_url = _post_auth_url_with_next(
        request,
        request.cookies.get("participant_app"),
        return_to,
        welcome=is_new,
    )
    redirect_url = _post_auth_or_onboarding_url(
        user,
        final_url,
        participant_app=request.cookies.get("participant_app"),
    )
    resp = RedirectResponse(redirect_url, status_code=303)
    policy = _cookie_policy(request)
    _set_user_cookie(resp, request, user)
    resp.delete_cookie("oauth_state", samesite=policy["samesite"], secure=policy["secure"])
    _clear_auth_return(resp, request)
    if is_new:
        _clear_pending_referral(resp, request)
    return resp


__all__ = ['google_login', 'google_callback', 'linkedin_login', 'linkedin_callback']
