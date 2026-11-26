"""Features / auth / routes."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from fastapi import Cookie
from fastapi import Depends
from fastapi import Form
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional
from app.ai_growth.security import is_safe_internal_next
from app.bootstrap.runtime import pwd_context
from app.bootstrap.runtime import templates
from app.bootstrap.settings import VERIFICATION_CODE_MAX_PER_HOUR
from app.bootstrap.settings import VERIFICATION_CODE_RESEND_COOLDOWN_SECONDS
from app.bot_defense import check_rate_limit
from app.bot_defense import client_ip
from app.bot_defense import honeypot_triggered
from app.bot_defense import make_captcha
from app.bot_defense import turnstile_site_key
from app.bot_defense import verify_bot_challenge
from app.database import get_db
from app.models import EmailVerificationCode
from app.models import User
from app.models import Verification
from app.verification.assurance import TIER_BASIC
from app.verification.assurance import evidence_reference


async def check_email_exists(email: str, db: Session = Depends(get_db)):
    from app.features.auth.policy import _is_email_blocked
    from app.features.auth.policy import _normalize_email

    normalized = _normalize_email(email)
    if not normalized:
        return JSONResponse({"exists": False})
    if _is_email_blocked(db, normalized):
        return JSONResponse({"exists": False})
    exists = db.query(User).filter(User.email == normalized).first() is not None
    return JSONResponse({"exists": exists})


async def send_auth_code(
    request: Request,
    email: str = Form(...),
    purpose: str = Form(...),
    db: Session = Depends(get_db)
):
    from app.features.auth.codes import _issue_verification_code
    from app.features.auth.codes import _latest_active_verification_code
    from app.features.auth.codes import _mask_email
    from app.features.auth.policy import _blocked_auth_message
    from app.features.auth.policy import _is_email_blocked
    from app.features.auth.policy import _local_dev_email_codes_enabled
    from app.features.auth.policy import _normalize_email
    from app.features.messaging.verification_messages import _send_verification_email

    normalized_email = _normalize_email(email)
    normalized_purpose = (purpose or "").strip().lower()

    # ive added this for phase 4 — stop bots hammering the email sender
    ip = client_ip(request)
    if not check_rate_limit(f"send-code:ip:{ip}", max_hits=8, window_seconds=600):
        return JSONResponse({"ok": False, "message": "Too many requests. Please wait a few minutes and try again."}, status_code=429)
    if normalized_email and not check_rate_limit(f"send-code:email:{normalized_email}", max_hits=5, window_seconds=600):
        return JSONResponse({"ok": False, "message": "Too many codes requested for this email. Please wait a few minutes."}, status_code=429)

    if not normalized_email:
        return JSONResponse({"ok": False, "message": "Please enter your email address first."}, status_code=400)
    if normalized_purpose not in {"register", "reset_password"}:
        return JSONResponse({"ok": False, "message": "Unsupported verification purpose."}, status_code=400)
    if _is_email_blocked(db, normalized_email):
        return JSONResponse({"ok": False, "message": _blocked_auth_message()}, status_code=403)

    existing_user = db.query(User).filter(User.email == normalized_email).first()

    if normalized_purpose == "register" and existing_user:
        return JSONResponse({"ok": False, "message": "This email is already registered. Please sign in instead."}, status_code=400)
    if normalized_purpose == "reset_password" and not existing_user:
        return JSONResponse({"ok": False, "message": "No account found with this email."}, status_code=400)

    now = datetime.utcnow()
    active_code = _latest_active_verification_code(db, normalized_email, normalized_purpose)
    if active_code and active_code.created_at and active_code.created_at > now - timedelta(seconds=VERIFICATION_CODE_RESEND_COOLDOWN_SECONDS):
        wait_seconds = max(1, VERIFICATION_CODE_RESEND_COOLDOWN_SECONDS - int((now - active_code.created_at).total_seconds()))
        return JSONResponse({
            "ok": False,
            "message": f"Please wait {wait_seconds} seconds before requesting another code."
        }, status_code=429)

    recent_code_count = db.query(EmailVerificationCode).filter(
        EmailVerificationCode.email == normalized_email,
        EmailVerificationCode.purpose == normalized_purpose,
        EmailVerificationCode.created_at >= now - timedelta(hours=1),
    ).count()
    if recent_code_count >= VERIFICATION_CODE_MAX_PER_HOUR and not active_code:
        return JSONResponse({
            "ok": False,
            "message": "Too many verification requests. Please try again later."
        }, status_code=429)

    code, created_new = _issue_verification_code(db, normalized_email, normalized_purpose)
    sent, send_error = _send_verification_email(normalized_email, normalized_purpose, code)
    if not sent:
        if _local_dev_email_codes_enabled(request):
            print(f"Local dev verification code for {normalized_email} ({normalized_purpose}): {code}")
            return JSONResponse({
                "ok": True,
                "message": f"Local dev mode: use verification code {code}.",
                "dev_code": code,
            })
        if created_new:
            db.query(EmailVerificationCode).filter(
                EmailVerificationCode.email == normalized_email,
                EmailVerificationCode.purpose == normalized_purpose,
                EmailVerificationCode.code == code,
                EmailVerificationCode.used_at.is_(None),
            ).delete(synchronize_session=False)
            db.commit()
        print(f"Verification email failed for {_mask_email(normalized_email)}: {send_error}")
        return JSONResponse({
            "ok": False,
            "message": "We could not send the verification email. Please try again in a moment."
        }, status_code=502)

    if _local_dev_email_codes_enabled(request):
        print(f"Local dev verification code for {normalized_email} ({normalized_purpose}): {code}")
        return JSONResponse({
            "ok": True,
            "message": f"Verification code sent to {_mask_email(normalized_email)}. Local dev code: {code}",
            "dev_code": code,
        })

    return JSONResponse({
        "ok": True,
        "message": f"Verification code sent to {_mask_email(normalized_email)}."
    })


def login_page(
    request: Request,
    success: Optional[str] = None,
    reset_success: Optional[str] = None,
    email: Optional[str] = None,
    oauth_error: Optional[str] = None,
    next: Optional[str] = None,
    role: Optional[str] = None,
    participant_app: Optional[str] = Cookie(None),
):
    from app.features.auth.cookies import _clear_participant_app
    from app.features.auth.cookies import _mark_participant_app
    from app.features.auth.cookies import _remember_auth_return
    from app.features.auth.navigation import _auth_uses_participant_app
    from app.features.auth.policy import _normalize_email
    from app.web.middleware import no_store_response

    normalized_role = role if role in {"participant", "researcher"} else ""
    response = no_store_response(templates.TemplateResponse("login.html", {
        "request": request,
        "error": oauth_error or None,
        "success": success,
        "reset_error": None,
        "reset_success": reset_success,
        "reset_open": False,
        "login_email": _normalize_email(email or ""),
        "reset_email": _normalize_email(email or ""),
        "login_next": next if is_safe_internal_next(next) else "",
        "login_role": normalized_role,
        "participant_app": _auth_uses_participant_app(request, normalized_role, participant_app),
    }))
    if normalized_role == "participant":
        _mark_participant_app(response, request)
    elif normalized_role == "researcher":
        _clear_participant_app(response, request)
    _remember_auth_return(response, request, next)
    return response


def login(
    request: Request,
    email: Optional[str] = Form(None),
    password: str = Form(...),
    next: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    participant_app: Optional[str] = Cookie(None),
    auth_return_to: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    from app.features.analytics.events import _record_user_event
    from app.features.auth.cookies import _clear_auth_return
    from app.features.auth.cookies import _clear_participant_app
    from app.features.auth.cookies import _mark_participant_app
    from app.features.auth.cookies import _safe_auth_return
    from app.features.auth.cookies import _set_user_cookie
    from app.features.auth.navigation import _auth_uses_participant_app
    from app.features.auth.navigation import _post_auth_or_onboarding_url
    from app.features.auth.navigation import _post_auth_url_with_next
    from app.features.auth.policy import _blocked_auth_message
    from app.features.auth.policy import _is_email_blocked
    from app.features.auth.policy import _normalize_email
    from app.web.middleware import no_store_response

    next_url = _safe_auth_return(next, auth_return_to)
    normalized_email = _normalize_email(email or "")
    # ive added this for phase 4 — slow down password guessing per ip
    if not check_rate_limit(f"login:ip:{client_ip(request)}", max_hits=10, window_seconds=300):
        normalized_role = role if role in {"participant", "researcher"} else ""
        return no_store_response(templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Too many login attempts. Please wait a few minutes and try again.",
            "success": None, "reset_error": None, "reset_success": None,
            "reset_open": False, "login_email": normalized_email, "reset_email": "",
            "login_next": next if is_safe_internal_next(next) else "",
            "login_role": normalized_role,
            "participant_app": _auth_uses_participant_app(request, normalized_role, participant_app),
        }))

    if _is_email_blocked(db, normalized_email):
        normalized_role = role if role in {"participant", "researcher"} else ""
        return no_store_response(templates.TemplateResponse("login.html", {
            "request": request,
            "error": _blocked_auth_message(),
            "success": None, "reset_error": None, "reset_success": None,
            "reset_open": False, "login_email": normalized_email, "reset_email": "",
            "login_next": next_url,
            "login_role": normalized_role,
            "participant_app": _auth_uses_participant_app(request, normalized_role, participant_app),
        }))
    try:
        user = db.query(User).filter(User.email == normalized_email).first()
        if not user and normalized_email:
            matches = db.query(User).filter(
                func.lower(func.trim(User.username)) == normalized_email
            ).limit(2).all()
            # Usernames predate unique handles; ambiguous names must use email.
            user = matches[0] if len(matches) == 1 else None
    except Exception as e:
        normalized_role = role if role in {"participant", "researcher"} else ""
        return no_store_response(templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Sign-in is temporarily unavailable. Please try again.",
            "success": None, "reset_error": None, "reset_success": None,
            "reset_open": False, "login_email": normalized_email, "reset_email": "",
            "login_next": next_url,
            "login_role": normalized_role,
            "participant_app": _auth_uses_participant_app(request, normalized_role, participant_app),
        }))

    if not user or _is_email_blocked(db, user.email) or not pwd_context.verify(password, user.password):
        normalized_role = role if role in {"participant", "researcher"} else ""
        return no_store_response(templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid email / username or password. If your username is shared, use your email address.",
            "success": None, "reset_error": None, "reset_success": None,
            "reset_open": False, "login_email": normalized_email, "reset_email": "",
            "login_next": next_url,
            "login_role": normalized_role,
            "participant_app": _auth_uses_participant_app(request, normalized_role, participant_app),
        }))

    final_url = _post_auth_url_with_next(request, participant_app, next_url, role)
    response = RedirectResponse(
        _post_auth_or_onboarding_url(user, final_url, role=role, participant_app=participant_app),
        status_code=303,
    )
    _record_user_event(db, request, "login", user=user, metadata={"role": role or "", "user_role": role or "", "next": next_url or "", "source": "Login"})
    db.commit()
    _set_user_cookie(response, request, user)
    _clear_auth_return(response, request)
    if (role or "").strip().lower() == "participant":
        _mark_participant_app(response, request)
    elif (role or "").strip().lower() == "researcher":
        _clear_participant_app(response, request)
    return response


def password_reset(
    request: Request,
    email: str = Form(...),
    verification_code: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    from app.features.auth.codes import _consume_verification_code
    from app.features.auth.policy import _blocked_auth_message
    from app.features.auth.policy import _is_email_blocked
    from app.features.auth.policy import _normalize_email
    from app.web.middleware import no_store_response

    normalized_email = _normalize_email(email)
    if _is_email_blocked(db, normalized_email):
        return no_store_response(templates.TemplateResponse("login.html", {
            "request": request,
            "error": None, "success": None,
            "reset_error": _blocked_auth_message(), "reset_success": None,
            "reset_open": True,
            "login_email": "", "reset_email": normalized_email,
        }))
    user = db.query(User).filter(User.email == normalized_email).first()

    def reset_error(msg):
        return no_store_response(templates.TemplateResponse("login.html", {
            "request": request,
            "error": None, "success": None,
            "reset_error": msg, "reset_success": None,
            "reset_open": True,
            "login_email": "", "reset_email": normalized_email,
        }))

    if not user:
        return reset_error("No account found with this email.")
    if not new_password or not confirm_password:
        return reset_error("Please enter and confirm your new password.")
    if new_password != confirm_password:
        return reset_error("The two passwords do not match.")
    if len(new_password) < 6:
        return reset_error("New password must be at least 6 characters.")
    if not _consume_verification_code(db, normalized_email, "reset_password", verification_code):
        return reset_error("Invalid or expired verification code.")

    user.password = pwd_context.hash(new_password)
    db.commit()

    return no_store_response(templates.TemplateResponse("login.html", {
        "request": request,
        "error": None, "success": None,
        "reset_error": None,
        "reset_success": "Password updated successfully. Please sign in with your new password.",
        "reset_open": False,
        "login_email": normalized_email, "reset_email": "",
    }))


def show_register(
    request: Request,
    role: Optional[str] = None,
    next: Optional[str] = None,
    ref: Optional[str] = None,
    participant_app: Optional[str] = Cookie(None),
    pending_referral: Optional[str] = Cookie(None),
):
    from app.features.auth.cookies import _clear_participant_app
    from app.features.auth.cookies import _mark_participant_app
    from app.features.auth.cookies import _remember_auth_return
    from app.features.auth.navigation import _auth_uses_participant_app
    from app.features.auth.referrals import _normalize_referral_code
    from app.features.auth.referrals import _remember_pending_referral
    from app.web.middleware import no_store_response

    normalized_role = role if role in {"participant", "researcher"} else ""
    # ive added this for phase 4 — every register page load gets a fresh captcha
    captcha_question, captcha_token = make_captcha()
    register_next = next if is_safe_internal_next(next) else ""
    referral_code = _normalize_referral_code(ref) or _normalize_referral_code(pending_referral)
    response = no_store_response(templates.TemplateResponse(
        "register.html",
        {
            "request": request, "error": None, "register_email": "", "register_phone": "", "register_code": "",
            "register_step": 1,
            "register_role": normalized_role,
            "register_next": register_next,
            "register_referral": referral_code,
            "register_values": {},
            "birth_year_max": datetime.now(timezone.utc).year - 18,
            "participant_app": _auth_uses_participant_app(request, normalized_role, participant_app),
            "captcha_question": captcha_question, "captcha_token": captcha_token,
            "turnstile_site_key": turnstile_site_key(),
        }
    ))
    if normalized_role == "participant":
        _mark_participant_app(response, request)
    elif normalized_role == "researcher":
        _clear_participant_app(response, request)
    _remember_auth_return(response, request, register_next)
    if referral_code:
        _remember_pending_referral(response, request, referral_code)
    return response


async def do_register(
    request: Request,
    participant_app: Optional[str] = Cookie(None),
    auth_return_to: Optional[str] = Cookie(None),
    pending_referral: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    from app.features.analytics.events import _record_user_event
    from app.features.auth.codes import _consume_verification_code
    from app.features.auth.codes import _validate_registration_password
    from app.features.auth.cookies import _clear_auth_return
    from app.features.auth.cookies import _clear_participant_app
    from app.features.auth.cookies import _mark_participant_app
    from app.features.auth.cookies import _safe_auth_return
    from app.features.auth.cookies import _set_user_cookie
    from app.features.auth.navigation import _auth_uses_participant_app
    from app.features.auth.navigation import _post_auth_or_onboarding_url
    from app.features.auth.navigation import _post_auth_url_with_next
    from app.features.auth.policy import _blocked_auth_message
    from app.features.auth.policy import _is_email_blocked
    from app.features.auth.policy import _normalize_email
    from app.features.auth.referrals import _apply_referral_code
    from app.features.auth.referrals import _clear_pending_referral
    from app.features.auth.referrals import _find_referrer_by_code
    from app.features.auth.referrals import _generate_referral_code
    from app.features.auth.referrals import _normalize_referral_code
    from app.features.auth.referrals import _remember_pending_referral
    from app.features.matching.eligibility import _age_range_from_birth_date
    from app.features.trust.risk import _record_risk_signal
    from app.web.middleware import no_store_response

    form = await request.form()
    email = _normalize_email(form.get("email") or "")
    phone_number = (form.get("phone_number") or "").strip()
    password = form.get("password") or ""
    confirm = form.get("confirm") or ""
    verification_code = form.get("verification_code") or ""
    referral_input = _normalize_referral_code(form.get("referral_code") or pending_referral)
    next_url = _safe_auth_return(form.get("next"), auth_return_to)
    role = (form.get("role") or "").strip().lower()
    role = role if role in {"participant", "researcher"} else None
    register_values = {
        "first_name": (form.get("first_name") or "").strip(),
        "last_name": (form.get("last_name") or "").strip(),
        "username": (form.get("username") or "").strip(),
        "birth_year": (form.get("birth_year") or "").strip(),
        "birth_month": (form.get("birth_month") or "").strip(),
        "education_level": (form.get("education_level") or "").strip(),
        "field": (form.get("field") or "").strip(),
        "status": (form.get("status") or "").strip(),
        "current_province": (form.get("current_province") or "").strip(),
        "language": (form.get("language") or "").strip(),
        "participation_format": (form.get("participation_format") or "").strip(),
    }

    def reg_error(msg, step: int = 1):
        # phase 4 — reissue a fresh captcha with every error page
        new_question, new_token = make_captcha()
        response = no_store_response(templates.TemplateResponse("register.html", {
            "request": request, "error": msg, "register_email": email,
            "register_phone": phone_number,
            "register_code": verification_code,
            "register_step": step,
            "register_role": role or "",
            "register_next": next_url if is_safe_internal_next(next_url) else "",
            "register_referral": referral_input,
            "register_values": register_values,
            "birth_year_max": datetime.now(timezone.utc).year - 18,
            "participant_app": _auth_uses_participant_app(request, role, participant_app),
            "captcha_question": new_question, "captcha_token": new_token,
            "turnstile_site_key": turnstile_site_key(),
        }))
        if role == "researcher":
            _clear_participant_app(response, request)
        if referral_input:
            _remember_pending_referral(response, request, referral_input)
        return response

    # ive added these for phase 4 — bot checks before we even validate the form
    if honeypot_triggered(form):
        # dont tell the bot what went wrong
        return reg_error("Something went wrong. Please try again.")
    if not check_rate_limit(f"register:ip:{client_ip(request)}", max_hits=5, window_seconds=600):
        return reg_error("Too many attempts. Please wait a few minutes and try again.")
    challenge_ok = await verify_bot_challenge(
        managed_token=form.get("cf-turnstile-response"),
        fallback_token=form.get("captcha_token"),
        fallback_answer=form.get("captcha_answer"),
        remote_ip=client_ip(request),
    )
    if not challenge_ok:
        return reg_error("We could not validate the anti-bot check. Please try again.", step=2)

    if not email: return reg_error("Email is required.")
    if _is_email_blocked(db, email):
        return reg_error(_blocked_auth_message())
    if password != confirm: return reg_error("Passwords do not match.", step=2)
    pw_error = _validate_registration_password(password)
    if pw_error: return reg_error(pw_error, step=2)
    required_demo_fields = {
        "first_name": "First name is required.",
        "last_name": "Last name is required.",
        "username": "Username is required.",
        "birth_year": "Birth year is required.",
        "birth_month": "Birth month is required.",
        "education_level": "Education level is required.",
        "field": "Field or major is required.",
        "status": "Current status is required.",
        "current_province": "Current state is required.",
        "language": "Primary language is required.",
        "participation_format": "Participation preference is required.",
    }
    for field_name, message in required_demo_fields.items():
        if not register_values.get(field_name):
            return reg_error(message, step=2)
    derived_age_range = _age_range_from_birth_date(register_values["birth_year"], register_values["birth_month"])
    if not derived_age_range:
        return reg_error("Please enter a valid birth year and month. Participants must be 18 or older.", step=2)
    if db.query(User).filter(User.email == email).first():
        return reg_error("Email already exists.")
    if referral_input and not _find_referrer_by_code(db, referral_input):
        return reg_error("Referral code not found.")
    if not _consume_verification_code(db, email, "register", verification_code):
        return reg_error("Invalid or expired verification code.")

    user = User(
        email=email,
        password=pwd_context.hash(password),
        phone_number=phone_number or None,
        first_name=register_values["first_name"],
        last_name=register_values["last_name"],
        username=register_values["username"],
        birth_year=register_values["birth_year"],
        birth_month=register_values["birth_month"],
        age_range=derived_age_range,
        education_level=register_values["education_level"],
        field=register_values["field"],
        status=register_values["status"],
        current_country="United States",
        current_province=register_values["current_province"],
        state=register_values["current_province"],
        language=register_values["language"],
        participation_format=register_values["participation_format"],
        referral_code=_generate_referral_code(db),
    )
    referral_error = _apply_referral_code(db, user, referral_input)
    if referral_error:
        return reg_error(referral_error)
    db.add(user)
    db.flush()
    user.verification_status = "verified"
    user.verified_tier = TIER_BASIC
    user.verified_email = email
    user.verified_at = datetime.utcnow()
    db.add(Verification(
        user_id=user.id,
        attribute="email_control",
        method="email_otp",
        status="verified",
        trust_score=0.35,
        evidence_ref=evidence_reference("email_otp", email),
        verified_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=365),
        verified_by="system",
    ))
    _record_risk_signal(
        db,
        request,
        user=user,
        event_type="registration",
        bot_challenge_passed=True,
    )
    db.commit()
    db.refresh(user)
    _record_user_event(
        db,
        request,
        "sign_up",
        user=user,
        metadata={
            "role": role or "",
            "user_role": role or "",
            "next": next_url or "",
            "source": "Sign up",
            "referral_code": referral_input or "",
            "invited_by_user_id": user.invited_by_user_id or "",
        },
    )
    db.commit()

    final_url = _post_auth_url_with_next(request, participant_app, next_url, role=role, welcome=True)
    response = RedirectResponse(
        _post_auth_or_onboarding_url(user, final_url, role=role, participant_app=participant_app),
        status_code=303,
    )
    _set_user_cookie(response, request, user)
    _clear_auth_return(response, request)
    _clear_pending_referral(response, request)
    if role == "participant":
        _mark_participant_app(response, request)
    elif role == "researcher":
        _clear_participant_app(response, request)
    return response


def logout(request: Request):
    from app.features.auth.cookies import _cookie_policy

    response = RedirectResponse("/login", status_code=303)
    policy = _cookie_policy(request)
    response.delete_cookie("user_id", samesite=policy["samesite"], secure=policy["secure"])
    return response


__all__ = ['check_email_exists', 'send_auth_code', 'login_page', 'login', 'password_reset', 'show_register', 'do_register', 'logout']
