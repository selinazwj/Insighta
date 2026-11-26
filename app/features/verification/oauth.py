"""Features / verification / oauth."""

from datetime import datetime
from fastapi import Depends
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import Optional
from urllib.parse import urlencode
import httpx
import secrets
from app.bootstrap.settings import LINKEDIN_AUTH_URL
from app.bootstrap.settings import LINKEDIN_CLIENT_ID
from app.bootstrap.settings import LINKEDIN_CLIENT_SECRET
from app.bootstrap.settings import LINKEDIN_TOKEN_URL
from app.bootstrap.settings import LINKEDIN_USERINFO_URL
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import User
from app.verification.assurance import TIER_TRUSTED


def verify_linkedin_start(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # ive added this for verification — start LinkedIn OAuth in verify mode for the logged in user
    from app.features.auth.cookies import _cookie_policy
    from app.features.auth.oauth_settings import _linkedin_redirect_uri
    from app.features.verification.service import _current_assurance_tier
    from app.features.verification.service import _tier_rank

    if not LINKEDIN_CLIENT_ID:
        return RedirectResponse("/verify?verify_error=LinkedIn+verification+is+not+configured+yet.", status_code=302)
    # phase 3 — allow upgrades: only skip if the user already holds tier_2 or better
    if _tier_rank(_current_assurance_tier(db, current_user)) <= 2:
        return RedirectResponse("/verify", status_code=302)
    state = secrets.token_urlsafe(16)
    params = urlencode({
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": _linkedin_redirect_uri("/verify/linkedin/callback"),
        "state": state,
        "scope": "openid profile email",
    })
    response = RedirectResponse(f"{LINKEDIN_AUTH_URL}?{params}", status_code=302)
    # separate cookie from the login flow so the two dont clash
    response.set_cookie("verify_oauth_state", state, max_age=300, **_cookie_policy(request))
    return response


async def verify_linkedin_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # ive added this for verification — LinkedIn came back, mark the logged in user verified tier_2
    from app.features.auth.cookies import _cookie_policy
    from app.features.auth.oauth_settings import _linkedin_redirect_uri
    from app.features.auth.policy import _normalize_email
    from app.features.verification.service import _record_verified_evidence
    from app.features.verification.service import _tier_rank
    from app.features.verification.service import _verified_evidence_owner

    if error:
        return RedirectResponse("/verify?verify_error=LinkedIn+verification+was+cancelled.", status_code=302)

    stored_state = request.cookies.get("verify_oauth_state")
    if not state or state != stored_state:
        return RedirectResponse("/verify?verify_error=Invalid+state.+Please+try+again.", status_code=302)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _linkedin_redirect_uri("/verify/linkedin/callback"),
                "client_id": LINKEDIN_CLIENT_ID,
                "client_secret": LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return RedirectResponse("/verify?verify_error=LinkedIn+token+exchange+failed.", status_code=302)

        userinfo_resp = await client.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo = userinfo_resp.json()

    linkedin_id = str(userinfo.get("sub", ""))
    if not linkedin_id:
        return RedirectResponse("/verify?verify_error=Could+not+read+your+LinkedIn+profile.", status_code=302)
    evidence_owner = _verified_evidence_owner(
        db,
        method="linkedin_oauth",
        subject=linkedin_id,
        excluding_user_id=current_user.id,
    )
    if evidence_owner:
        return RedirectResponse(
            "/verify?verify_error=That+LinkedIn+account+is+already+linked+to+another+participant.",
            status_code=302,
        )

    # Keep the legacy login fields when available, and always store a separate
    # verification evidence record so Google login and LinkedIn evidence can coexist.
    if not current_user.oauth_provider:
        current_user.oauth_provider = "linkedin"
        current_user.oauth_id = linkedin_id
    _record_verified_evidence(
        db,
        user_id=current_user.id,
        attribute="professional_account_control",
        method="linkedin_oauth",
        subject=linkedin_id,
        trust_score=0.65,
    )
    # record the linkedin email so domain-restricted studies can check it too
    linkedin_email = _normalize_email(userinfo.get("email", ""))
    if linkedin_email:
        current_user.verified_email = linkedin_email
    # Store LinkedIn as additional reusable account evidence.
    if _tier_rank(getattr(current_user, "verified_tier", None)) > 2:
        current_user.verified_tier = TIER_TRUSTED
    current_user.verification_status = "verified"
    current_user.verified_at = datetime.utcnow()
    db.commit()

    policy = _cookie_policy(request)
    # done=1 shows the clean success screen instead of more tier options
    resp = RedirectResponse("/verify?done=1", status_code=303)
    resp.delete_cookie("verify_oauth_state", samesite=policy["samesite"], secure=policy["secure"])
    return resp


__all__ = ['verify_linkedin_start', 'verify_linkedin_callback']
