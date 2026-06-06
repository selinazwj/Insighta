from fastapi import FastAPI, APIRouter, Request, Form, Depends, HTTPException, Cookie, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func, JSON, Boolean, case, inspect, text
from pathlib import Path
from typing import Any, List, Optional
from datetime import datetime, timedelta, timezone, date, time
from urllib.parse import urlencode
from io import BytesIO
import random
import re
import shutil
import uuid
import os
import secrets
import hashlib
import stripe
import smtplib
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import engine, get_db
from app.models import Base, User, Survey, Response, Feedback, Notification, EmailVerificationCode, Question, Answer, ResponseQualityCheck, QualityBlacklist, SupportThread, SupportMessage
from app.quality_engine import (
    anthropic_api_key_configured,
    batch_anomaly_scores_for_features,
    compute_excel_row_quality,
    create_excel_quality_check,
    _duration_seconds_between,
    apply_auto_approve_checks,
    ensure_builtin_quality_checks,
    evaluate_builtin_response,
    resolve_excel_row_context,
    upsert_builtin_quality_check,
)
from app.payouts import (
    APPROVED,
    LEGACY_RELEASED,
    mark_response_under_review,
    reject_response_payout,
    release_response_payout,
    return_response_to_review,
)
from app.verification.routes import router as verification_router
from app.ai_growth.routes import router as ai_growth_router
from app.ai_growth.security import is_safe_internal_next
from app.ai_growth.jump import mark_latest_jump_completed_for_response
from app.ai_growth.prediction import recommend_surveys_for_user

app = FastAPI()
app.include_router(verification_router)
app.include_router(ai_growth_router)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

def no_store_response(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
Base.metadata.create_all(bind=engine)

def ensure_user_profile_columns():
    columns = {col["name"] for col in inspect(engine).get_columns("users")}
    profile_columns = {
        "phone_number": "VARCHAR",
        "birth_year": "VARCHAR",
        "birth_month": "VARCHAR",
        "profile_description": "VARCHAR",
        "current_country": "VARCHAR",
        "current_province": "VARCHAR",
        "current_city": "VARCHAR",
        "origin_country": "VARCHAR",
        "origin_province": "VARCHAR",
        "origin_city": "VARCHAR",
        "race": "VARCHAR",
        "income_level": "VARCHAR",
        "lifestyle_tags": "VARCHAR",
    }
    with engine.begin() as conn:
        for name, column_type in profile_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {column_type}"))

ensure_user_profile_columns()

def ensure_survey_listing_columns():
    columns = {col["name"] for col in inspect(engine).get_columns("surveys")}
    listing_columns = {
        "target_income_level": "VARCHAR",
        "target_lifestyle_tags": "VARCHAR",
        "target_niche_requirements": "VARCHAR",
        "raffle_prize_type": "VARCHAR",
        "quality_auto_filter_enabled": "BOOLEAN DEFAULT false",
        "quality_auto_filter_min_score": "FLOAT DEFAULT 80.0",
    }
    with engine.begin() as conn:
        for name, column_type in listing_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE surveys ADD COLUMN {name} {column_type}"))

ensure_survey_listing_columns()

def ensure_response_tracking_columns():
    columns = {col["name"] for col in inspect(engine).get_columns("responses")}
    response_columns = {
        "client_ip": "VARCHAR",
        "user_agent": "VARCHAR",
        "device_fingerprint": "VARCHAR",
    }
    with engine.begin() as conn:
        for name, column_type in response_columns.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE responses ADD COLUMN {name} {column_type}"))

ensure_response_tracking_columns()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
VERIFICATION_CODE_EXPIRE_MINUTES = 10

# ---------------------------
# OAuth 2.0 configuration
# ---------------------------
BASE_URL = os.environ.get("BASE_URL", "https://insightaco.org")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

LINKEDIN_CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET")
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

def send_email(to: str, subject: str, body: str):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to, msg.as_string())
    except Exception as e:
        print(f"Email error: {e}")

router = APIRouter()


# ---------------------------
# Commission helper
# ---------------------------

def calculate_commission(per_person_gross: float):
    if per_person_gross >= 25:
        rate = 0.25
    elif per_person_gross >= 15:
        rate = 0.20
    else:
        rate = 0.15
    reward = round(per_person_gross * (1 - rate), 2)
    return rate, reward

def timeline_multiplier(urgency_level: Optional[str]) -> float:
    urgency = (urgency_level or "flexible").strip().lower()
    if urgency == "within_1_week":
        return 1.10
    if urgency == "within_1_month":
        return 1.05
    return 1.0

def volunteer_platform_fee(target_responses: int) -> float:
    return round(((max(int(target_responses or 0), 1) + 9) // 10) * 1.0, 2)


def _extract_client_meta(request: Request, current_user: User) -> dict:
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else None
    if not client_ip and request.client:
        client_ip = request.client.host
    user_agent = request.headers.get("user-agent")
    fingerprint = request.headers.get("x-device-fingerprint")
    if not fingerprint:
        raw = f"{user_agent or ''}|{getattr(current_user, 'device_type', '') or ''}|{current_user.id}"
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return {
        "client_ip": client_ip,
        "user_agent": user_agent,
        "device_fingerprint": fingerprint,
    }


def _get_quality_check_for_publisher(db: Session, check_id: int, publisher_id: int):
    row = db.query(ResponseQualityCheck).filter(ResponseQualityCheck.id == check_id).first()
    if not row:
        raise HTTPException(404, "Quality check not found")
    survey = db.query(Survey).filter(
        Survey.id == row.survey_id,
        Survey.publisher_id == publisher_id,
    ).first()
    if not survey:
        raise HTTPException(403, "Not allowed")
    return row, survey


# ---------------------------
# Matching helpers
# ---------------------------

EDUCATION_RANK = {
    "Below High School": 0,
    "High School": 1,
    "Undergraduate": 2,
    "Graduate": 3,
    "PhD": 4,
    "Postdoc": 5,
}

def _education_rank(level: Optional[str], fallback: int) -> int:
    if not level:
        return fallback
    return EDUCATION_RANK.get(level, fallback)

def _age_range_from_birth_date(birth_year: Optional[str], birth_month: Optional[str]) -> Optional[str]:
    if not birth_year:
        return None
    try:
        year = int(birth_year)
        month = int(birth_month or "1")
    except ValueError:
        return None
    today = datetime.now(timezone.utc)
    age = today.year - year - (1 if today.month < month else 0)
    if age < 18:
        return None
    if age <= 24:
        return "18-24"
    if age <= 34:
        return "25-34"
    if age <= 44:
        return "35-44"
    return "45+"

def _value_with_other(value: Optional[str], other_value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    other_value = (other_value or "").strip()
    if value in {"Other", "Prefer to self-describe"} and other_value:
        return f"{value}: {other_value}"
    return value or None

def _list_with_other(values: list[str], other_value: Optional[str]) -> list[str]:
    cleaned = [v.strip() for v in values if v and v.strip()]
    other_value = (other_value or "").strip()
    if "Other" in cleaned and other_value:
        cleaned.append(f"Other: {other_value}")
    return cleaned

def _is_empty(val: Optional[str]) -> bool:
    return val is None or val.strip() == "" or val.strip().lower() == "all"

def _field_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    if _is_empty(target):
        return True
    if not user_val:
        return False
    return target.strip().lower() == user_val.strip().lower()

def _location_matches(target: Optional[str], user: User) -> bool:
    if _is_empty(target):
        return True
    target_clean = target.strip().lower()
    user_values = [
        getattr(user, "state", None),
        getattr(user, "current_country", None),
        getattr(user, "current_province", None),
        getattr(user, "current_city", None),
    ]
    return any((value or "").strip().lower() == target_clean for value in user_values)

def _language_matches(target: Optional[str], user_languages: Optional[str]) -> bool:
    if _is_empty(target):
        return True
    if not user_languages:
        return False
    user_list = [lang.strip().lower() for lang in user_languages.split(",") if lang.strip()]
    return target.strip().lower() in user_list

def _tags_match(target_tags: Optional[str], user_tags: Optional[str]) -> bool:
    if _is_empty(target_tags):
        return True
    if not user_tags:
        return False
    target_set = {t.strip().lower() for t in target_tags.split(",") if t.strip()}
    user_set = {t.strip().lower() for t in user_tags.split(",") if t.strip()}
    return bool(target_set & user_set)

def _participation_format_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    if _is_empty(target) or (target and target.strip().lower() == "both"):
        return True
    if not user_val:
        return False
    if user_val.strip().lower() == "both":
        return True
    return target.strip().lower() == user_val.strip().lower()

def _device_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    if _is_empty(target) or (target and target.strip().lower() == "any"):
        return True
    if not user_val:
        return False
    if user_val.strip().lower() == "any":
        return True
    return target.strip().lower() == user_val.strip().lower()


# ---------------------------
# Other helpers
# ---------------------------

def _parse_optional_int(v) -> Optional[int]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None

def _normalize_task_type(value: Optional[str]) -> str:
    return "interview" if value == "interview" else "survey"

def _clean_target(val: Optional[str]) -> str:
    return '' if not val or val.strip().lower() == 'all' else val


def _normalize_excel_header(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _value_as_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(v) for v in value]
    return value


def _latest_excel_quality_rows(rows: list) -> list:
    excel_rows = [row for row in rows if row.source_type == "excel"]
    other_rows = [row for row in rows if row.source_type != "excel"]
    if not excel_rows:
        return rows

    def _upload_key(row):
        ref = row.source_ref or ""
        return ref.rsplit(":row_", 1)[0] if ":row_" in ref else ref

    latest_row = max(excel_rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc))
    latest_key = _upload_key(latest_row)
    latest_excel = [row for row in excel_rows if _upload_key(row) == latest_key]
    latest_excel.sort(key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return latest_excel + other_rows


def _quality_row_label(index: int = 0) -> str:
    return str(index + 1)


# ---------------------------
# Auth helpers
# ---------------------------

def _normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()

MOBILE_USER_AGENT_TOKENS = ("mobile", "android", "iphone", "ipad")

def _is_mobile_request(request: Request) -> bool:
    user_agent = request.headers.get("user-agent", "").lower()
    return any(token in user_agent for token in MOBILE_USER_AGENT_TOKENS)

def _participant_dashboard_url(request: Request) -> str:
    return "/dashboard/mobile" if _is_mobile_request(request) else "/dashboard"

def _should_use_participant_app(request: Request, participant_app: Optional[str] = None) -> bool:
    return _is_mobile_request(request) or participant_app == "1"

def _post_auth_url(request: Request, participant_app: Optional[str] = None, welcome: bool = False) -> str:
    if _should_use_participant_app(request, participant_app):
        base_url = _participant_dashboard_url(request)
    else:
        base_url = "/publisher"
    return f"{base_url}?welcome=1" if welcome else base_url

def _cookie_policy(request: Optional[Request]) -> dict:
    is_https = False
    if request is not None:
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        is_https = (request.url.scheme == "https") or (forwarded_proto == "https")
    if is_https:
        return {"httponly": True, "samesite": "none", "secure": True}
    return {"httponly": True, "samesite": "lax", "secure": False}

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

def _mark_participant_app(response: RedirectResponse, request: Optional[Request] = None):
    response.set_cookie(
        "participant_app",
        "1",
        max_age=60 * 60 * 24 * 30,
        **_cookie_policy(request),
    )

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

def _issue_verification_code(db: Session, email: str, purpose: str) -> str:
    _mark_previous_codes_used(db, email, purpose)
    code = _generate_verification_code()
    db.add(EmailVerificationCode(
        email=email,
        purpose=purpose,
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=VERIFICATION_CODE_EXPIRE_MINUTES)
    ))
    db.commit()
    return code

def _consume_verification_code(db: Session, email: str, purpose: str, code: str) -> bool:
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
    record.used_at = datetime.utcnow()
    db.commit()
    return True

def _send_verification_email(email: str, purpose: str, code: str):
    if purpose == "register":
        subject = "Insighta registration verification code"
        title = "Verify your email"
        body_text = "Use the following verification code to finish creating your Insighta account."
    else:
        subject = "Insighta password reset verification code"
        title = "Reset your password"
        body_text = "Use the following verification code to reset your Insighta password."

    body = f'''
    <div style="font-family: DM Sans, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a18;">
      <div style="margin-bottom: 20px;">
        <div style="font-family: Georgia, serif; font-size: 28px; color: #2d6a4f; margin-bottom: 8px;">Insighta</div>
        <div style="font-size: 13px; color: #8a8a82;">Secure verification</div>
      </div>
      <div style="background: #f3f1ea; border: 1px solid #e0ddd3; border-radius: 16px; padding: 24px;">
        <h2 style="font-family: Georgia, serif; font-size: 26px; margin: 0 0 10px; color: #1a1a18;">{title}</h2>
        <p style="font-size: 15px; line-height: 1.7; color: #4a4a44; margin: 0 0 20px;">{body_text}</p>
        <div style="font-size: 13px; color: #8a8a82; margin-bottom: 8px;">Verification code</div>
        <div style="font-size: 36px; font-weight: 700; letter-spacing: 8px; color: #2d6a4f; background: white; border: 1px solid #d6d0c1; border-radius: 12px; padding: 18px 20px; text-align: center;">
          {code}
        </div>
        <p style="font-size: 13px; line-height: 1.7; color: #8a8a82; margin: 20px 0 0;">
          This code expires in {VERIFICATION_CODE_EXPIRE_MINUTES} minutes. If you did not request this, you can safely ignore it.
        </p>
      </div>
      <p style="font-size: 12px; color: #8a8a82; margin-top: 20px;">Sent from Insighta: {EMAIL_ADDRESS}</p>
    </div>
    '''
    send_email(email, subject, body)


# ---------------------------
# Send verification code API
# ---------------------------

@app.get("/auth/check-email")
async def check_email_exists(email: str, db: Session = Depends(get_db)):
    normalized = _normalize_email(email)
    if not normalized:
        return JSONResponse({"exists": False})
    exists = db.query(User).filter(User.email == normalized).first() is not None
    return JSONResponse({"exists": exists})


@app.post("/auth/send-code")
async def send_auth_code(
    email: str = Form(...),
    purpose: str = Form(...),
    db: Session = Depends(get_db)
):
    normalized_email = _normalize_email(email)
    normalized_purpose = (purpose or "").strip().lower()

    if not normalized_email:
        return JSONResponse({"ok": False, "message": "Please enter your email address first."}, status_code=400)
    if normalized_purpose not in {"register", "reset_password"}:
        return JSONResponse({"ok": False, "message": "Unsupported verification purpose."}, status_code=400)

    existing_user = db.query(User).filter(User.email == normalized_email).first()

    if normalized_purpose == "register" and existing_user:
        return JSONResponse({"ok": False, "message": "This email is already registered. Please sign in instead."}, status_code=400)
    if normalized_purpose == "reset_password" and not existing_user:
        return JSONResponse({"ok": False, "message": "No account found with this email."}, status_code=400)

    code = _issue_verification_code(db, normalized_email, normalized_purpose)
    _send_verification_email(normalized_email, normalized_purpose, code)

    return JSONResponse({
        "ok": True,
        "message": f"Verification code sent to {_mask_email(normalized_email)}."
    })


# ---------------------------
# Google OAuth
# ---------------------------

@app.get("/auth/google")
def google_login(request: Request):
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


@app.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
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

    if not email:
        return RedirectResponse("/login?oauth_error=Could+not+retrieve+email+from+Google.", status_code=302)

    user = db.query(User).filter(User.email == email).first()
    is_new = False
    if not user:
        user = User(
            email=email,
            password=pwd_context.hash(secrets.token_urlsafe(32)),
            username=name,
            oauth_provider="google",
            oauth_id=google_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new = True
    else:
        if not user.oauth_provider:
            user.oauth_provider = "google"
            user.oauth_id = google_id
            db.commit()

    redirect_url = _post_auth_url(request, request.cookies.get("participant_app"), welcome=is_new)
    resp = RedirectResponse(redirect_url, status_code=303)
    policy = _cookie_policy(request)
    resp.set_cookie("user_id", str(user.id), **policy)
    resp.delete_cookie("oauth_state", samesite=policy["samesite"], secure=policy["secure"])
    return resp


# ---------------------------
# LinkedIn OAuth
# ---------------------------

@app.get("/auth/linkedin")
def linkedin_login(request: Request):
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(503, "LinkedIn login is not configured.")
    state = secrets.token_urlsafe(16)
    params = urlencode({
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/auth/linkedin/callback",
        "state": state,
        "scope": "openid profile email",
    })
    response = RedirectResponse(f"{LINKEDIN_AUTH_URL}?{params}", status_code=302)
    response.set_cookie("oauth_state", state, max_age=300, **_cookie_policy(request))
    return response


@app.get("/auth/linkedin/callback")
async def linkedin_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
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
                "redirect_uri": f"{BASE_URL}/auth/linkedin/callback",
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

    if not email:
        return RedirectResponse("/login?oauth_error=Could+not+retrieve+email+from+LinkedIn.", status_code=302)

    user = db.query(User).filter(User.email == email).first()
    is_new = False
    if not user:
        user = User(
            email=email,
            password=pwd_context.hash(secrets.token_urlsafe(32)),
            username=name,
            oauth_provider="linkedin",
            oauth_id=linkedin_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new = True
    else:
        if not user.oauth_provider:
            user.oauth_provider = "linkedin"
            user.oauth_id = linkedin_id
            db.commit()

    redirect_url = _post_auth_url(request, request.cookies.get("participant_app"), welcome=is_new)
    resp = RedirectResponse(redirect_url, status_code=303)
    policy = _cookie_policy(request)
    resp.set_cookie("user_id", str(user.id), **policy)
    resp.delete_cookie("oauth_state", samesite=policy["samesite"], secure=policy["secure"])
    return resp


# ---------------------------
# Index
# ---------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "show": None, "error": None}
    )


@app.get("/participant")
def participant_app_entry(request: Request, user_id: str = Cookie(None)):
    target_url = "/login?role=participant"
    if user_id:
        target_url = _participant_dashboard_url(request)
    response = RedirectResponse(target_url, status_code=302)
    _mark_participant_app(response, request)
    return response


@app.get("/participant/login")
def participant_login_entry(request: Request):
    response = RedirectResponse("/login?role=participant", status_code=302)
    _mark_participant_app(response, request)
    return response


# ---------------------------
# Login
# ---------------------------

@app.get("/login", response_class=HTMLResponse)
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
    normalized_role = role if role in {"participant", "researcher"} else ""
    return no_store_response(templates.TemplateResponse("login.html", {
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
        "participant_app": normalized_role == "participant" or _is_mobile_request(request),
    }))

@app.post("/login")
def login(
    request: Request,
    email: Optional[str] = Form(None),
    password: str = Form(...),
    next: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    participant_app: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    normalized_email = _normalize_email(email or "")
    try:
        user = db.query(User).filter(User.email == normalized_email).first()
    except Exception as e:
        return no_store_response(templates.TemplateResponse("login.html", {
            "request": request,
            "error": f"Database error: {e}",
            "success": None, "reset_error": None, "reset_success": None,
            "reset_open": False, "login_email": normalized_email, "reset_email": "",
            "login_next": next if is_safe_internal_next(next) else "",
            "login_role": role if role in {"participant", "researcher"} else "",
            "participant_app": role == "participant" or _is_mobile_request(request),
        }))

    if not user or not pwd_context.verify(password, user.password):
        return no_store_response(templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid email or password",
            "success": None, "reset_error": None, "reset_success": None,
            "reset_open": False, "login_email": normalized_email, "reset_email": "",
            "login_next": next if is_safe_internal_next(next) else "",
            "login_role": role if role in {"participant", "researcher"} else "",
            "participant_app": role == "participant" or _is_mobile_request(request),
        }))

    response = RedirectResponse(_post_auth_url_with_next(request, participant_app, next, role), status_code=303)
    response.set_cookie("user_id", str(user.id), **_cookie_policy(request))
    if (role or "").strip().lower() == "participant":
        _mark_participant_app(response, request)
    elif (role or "").strip().lower() == "researcher":
        policy = _cookie_policy(request)
        response.delete_cookie("participant_app", samesite=policy["samesite"], secure=policy["secure"])
    return response


# ---------------------------
# Password reset
# ---------------------------

@app.post("/password-reset")
def password_reset(
    request: Request,
    email: str = Form(...),
    verification_code: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    normalized_email = _normalize_email(email)
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


# ---------------------------
# Register
# ---------------------------

@app.get("/register", response_class=HTMLResponse)
def show_register(
    request: Request,
    role: Optional[str] = None,
    participant_app: Optional[str] = Cookie(None)
):
    normalized_role = role if role in {"participant", "researcher"} else ""
    return no_store_response(templates.TemplateResponse(
        "register.html",
        {
            "request": request, "error": None, "register_email": "", "register_phone": "", "register_code": "",
            "register_step": 1,
            "register_role": normalized_role,
            "participant_app": normalized_role == "participant" or participant_app == "1" or _is_mobile_request(request),
        }
    ))

@app.post("/register", response_class=HTMLResponse)
async def do_register(
    request: Request,
    participant_app: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    form = await request.form()
    email = _normalize_email(form.get("email") or "")
    phone_number = (form.get("phone_number") or "").strip()
    password = form.get("password") or ""
    confirm = form.get("confirm") or ""
    verification_code = form.get("verification_code") or ""
    role = (form.get("role") or "").strip().lower()
    role = role if role in {"participant", "researcher"} else None

    def reg_error(msg, step: int = 1):
        return no_store_response(templates.TemplateResponse("register.html", {
            "request": request, "error": msg, "register_email": email,
            "register_phone": phone_number,
            "register_code": verification_code,
            "register_step": step,
            "register_role": role or "",
            "participant_app": role == "participant" or _should_use_participant_app(request, participant_app),
        }))

    if not email: return reg_error("Email is required.")
    if password != confirm: return reg_error("Passwords do not match.", step=2)
    pw_error = _validate_registration_password(password)
    if pw_error: return reg_error(pw_error, step=2)
    if db.query(User).filter(User.email == email).first():
        return reg_error("Email already exists.")
    if not _consume_verification_code(db, email, "register", verification_code):
        return reg_error("Invalid or expired verification code.")

    user = User(
        email=email,
        password=pwd_context.hash(password),
        phone_number=phone_number or None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse(_post_auth_url_with_next(request, participant_app, role=role, welcome=True), status_code=303)
    response.set_cookie("user_id", str(user.id), **_cookie_policy(request))
    if role == "participant":
        _mark_participant_app(response, request)
    elif role == "researcher":
        policy = _cookie_policy(request)
        response.delete_cookie("participant_app", samesite=policy["samesite"], secure=policy["secure"])
    return response


# ---------------------------
# Current user
# ---------------------------

def get_current_user(
    user_id: str = Cookie(None),
    db: Session = Depends(get_db)
):
    if not user_id:
        raise HTTPException(401, "Not logged in")
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(401, "User not found")
    return user


# ---------------------------
# Choice page
# ---------------------------

@app.get("/choice", response_class=HTMLResponse)
def choice(request: Request, user_id: str = Cookie(None), db: Session = Depends(get_db)):
    if _is_mobile_request(request):
        target_url = _participant_dashboard_url(request) if user_id else "/participant"
    else:
        target_url = "/publisher" if user_id else "/login?role=researcher"
    return no_store_response(RedirectResponse(target_url, status_code=303))

# ---------------------------
# Guide page
# ---------------------------

@app.get("/guide", response_class=HTMLResponse)
def guide_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("guide.html", {"request": request, "current_user": current_user})

# ---------------------------
# Publisher Dashboard
# ---------------------------

@app.get("/publisher", response_class=HTMLResponse)
def publisher_dashboard(
    request: Request,
    user_id: str = Cookie(None),
    db: Session = Depends(get_db)
):
    if _is_mobile_request(request):
        return RedirectResponse(_participant_dashboard_url(request) if user_id else "/participant", status_code=302)

    if not user_id:
        return RedirectResponse("/login", status_code=303)
    try:
        current_user = db.query(User).filter(User.id == int(user_id)).first()
    except:
        return RedirectResponse("/login", status_code=303)
    if not current_user:
        return RedirectResponse("/login", status_code=303)
    all_items = db.query(Survey).filter(Survey.publisher_id == current_user.id).all()
    survey_ids = [s.id for s in all_items]

    if not survey_ids:
        completed_map = {}
    else:
        completed_map = dict(
            db.query(
                Response.survey_id,
                func.sum(case((Response.status == "completed", 1), else_=0)).label("completed_cnt"),
            )
            .filter(Response.survey_id.in_(survey_ids))
            .group_by(Response.survey_id)
            .all()
        )

    survey_items = [s for s in all_items if _normalize_task_type(getattr(s, "task_type", None)) == "survey"]
    interview_items = [s for s in all_items if _normalize_task_type(getattr(s, "task_type", None)) == "interview"]

    return templates.TemplateResponse(
        "publish.html",
        {
            "request": request,
            "surveys": survey_items,
            "interviews": interview_items,
            "completed_map": completed_map,
            "current_user": current_user
        }
    )

@app.get("/publisher/study/{survey_id}", response_class=HTMLResponse)
def publisher_study(
    survey_id: int, request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Not found")
    completed_count = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed"
    ).count()
    return templates.TemplateResponse("publisher_study.html", {
        "request": request,
        "survey": survey,
        "completed_count": completed_count,
        "current_user": current_user,
    })

# ---------------------------
# Delete survey
# ---------------------------
@app.post("/publisher/delete/{survey_id}")
def delete_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    # Delete answers first, then questions, then responses, then survey
    question_ids = [q.id for q in db.query(Question).filter(Question.survey_id == survey_id).all()]
    if question_ids:
        db.query(Answer).filter(Answer.question_id.in_(question_ids)).delete(synchronize_session=False)
    db.query(Question).filter(Question.survey_id == survey_id).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.survey_id == survey_id).delete(synchronize_session=False)
    db.query(Response).filter(Response.survey_id == survey_id).delete(synchronize_session=False)
    db.delete(survey)
    db.commit()
    return RedirectResponse("/publisher", status_code=303)

# ---------------------------
# Dashboard (participant view)
# ---------------------------

URGENCY_RANK = {
    "within_1_week":  3,
    "within_1_month": 2,
    "flexible":       1,
}

def _participant_survey_payload(s: Survey, db: Session, current_user: User, user_response: Optional[Response] = None) -> dict:
    completed_cnt = db.query(Response).filter(
        Response.survey_id == s.id, Response.status == "completed"
    ).count()
    if user_response is None:
        user_response = db.query(Response).filter(
            Response.survey_id == s.id, Response.participant_id == current_user.id
        ).first()

    category_images = {
        "research": "/static/psych.jpg", "life": "/static/campus_life.jpg",
        "clubs": "/static/fb.jpg", "market": "/static/habit.png",
        "academic": "/static/r2.jpg", "other": "/static/food.jpeg"
    }
    form_link = s.form_url if s.form_url and s.form_url != "__builtin__" else ""
    response_status = user_response.status if user_response else None

    return {
        "id": s.id,
        "title": s.title,
        "desc": s.description,
        "link": form_link,
        "type": _normalize_task_type(getattr(s, "task_type", None)),
        "category": s.category,
        "time": f"{s.estimated_time} min",
        "reward": f"${s.reward_amount:.2f}",
        "responses": f"{completed_cnt}/{s.target_responses}",
        "img": s.image_url if s.image_url else category_images.get(s.category, "/static/psych.jpg"),
        "is_started": response_status == "started",
        "is_completed": response_status == "completed",
        "is_skipped": response_status == "skipped",
        "status": response_status,
        "urgency": getattr(s, 'urgency_level', None) or 'flexible',
        "incentive_type": getattr(s, 'incentive_type', None),
    }

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if _is_mobile_request(request):
        tab = request.query_params.get("tab")
        mobile_url = f"/dashboard/mobile?tab={tab}" if tab in {"home", "earnings", "profile"} else "/dashboard/mobile"
        return RedirectResponse(mobile_url, status_code=302)
    all_published = db.query(Survey).filter(Survey.status == "published").all()

    user_edu_min = _education_rank(current_user.education_level, fallback=0)
    user_edu_max = _education_rank(current_user.education_level, fallback=999)

    def survey_matches(s: Survey) -> bool:
        if not _field_matches(s.target_age_range, current_user.age_range): return False
        if s.target_education_min is not None:
            if user_edu_min < s.target_education_min: return False
        if s.target_education_max is not None:
            if user_edu_max > s.target_education_max: return False
        if not _field_matches(s.target_field, current_user.field): return False
        if not _field_matches(s.target_status, current_user.status): return False
        if not _location_matches(s.target_state, current_user): return False
        if not _language_matches(s.target_language, current_user.language): return False
        if not _field_matches(s.target_ethnicity, current_user.ethnicity): return False
        if not _field_matches(s.target_sexual_orientation, current_user.sexual_orientation): return False
        if not _field_matches(s.target_mental_health_diagnosis, current_user.mental_health_diagnosis): return False
        if not _field_matches(s.target_physical_health_diagnosis, current_user.physical_health_diagnosis): return False
        if not _field_matches(s.target_sport_type, current_user.sport_type): return False
        if not _field_matches(s.target_sport_frequency, current_user.sport_frequency): return False
        if not _field_matches(s.target_smoking, current_user.smoking): return False
        if not _field_matches(s.target_cannabis_use, current_user.cannabis_use): return False
        if not _field_matches(getattr(s, 'target_student_status', None), getattr(current_user, 'student_status', None)): return False
        if not _field_matches(getattr(s, 'target_international_domestic', None), getattr(current_user, 'international_domestic', None)): return False
        if not _tags_match(getattr(s, 'target_experience_tags', None), getattr(current_user, 'experience_tags', None)): return False
        if not _participation_format_matches(getattr(s, 'target_participation_format', None), getattr(current_user, 'participation_format', None)): return False
        if not _device_matches(getattr(s, 'target_device', None), getattr(current_user, 'device_type', None)): return False
        if not _field_matches(getattr(s, 'target_income_level', None), getattr(current_user, 'income_level', None)): return False
        if not _tags_match(getattr(s, 'target_lifestyle_tags', None), getattr(current_user, 'lifestyle_tags', None)): return False
        return True

    matched = [s for s in all_published if survey_matches(s)]

    # LLM-only recommendation ranking: Claude provides completion_probability.
    # Urgency/date are no longer used as the recommendation score.
    recommendation_map = recommend_surveys_for_user(db, matched, current_user, use_cache=True)
    matched.sort(
        key=lambda s: (
            float(recommendation_map.get(s.id, {}).get("completion_probability") or 0.0),
            s.published_at.timestamp() if s.published_at else 0,
        ),
        reverse=True,
    )

    surveys_data = []
    for s in matched:
        llm_rec = recommendation_map.get(s.id, {})
        user_response = db.query(Response).filter(
            Response.survey_id == s.id, Response.participant_id == current_user.id
        ).first()
        payload = _participant_survey_payload(s, db, current_user, user_response)
        payload.update({
            "completion_probability": llm_rec.get("completion_probability"),
            "ai_confidence": llm_rec.get("confidence"),
            "why_recommended": (llm_rec.get("top_reasons") or [])[:3],
            "risk_reasons": (llm_rec.get("risk_reasons") or [])[:3],
            "recommended_action": llm_rec.get("recommended_action"),
            "ranking_note": llm_rec.get("ranking_note"),
            "model_version": llm_rec.get("model_version"),
        })
        surveys_data.append(payload)

    if timezone_offset is not None:
        user_tz = timezone(timedelta(minutes=-timezone_offset))
        now_user = datetime.now(user_tz)
        today_start_user = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_user.astimezone(timezone.utc)
    else:
        now_utc = datetime.now(timezone.utc)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    completed_today = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= today_start_utc
    ).count()

    pending_earnings = getattr(current_user, 'pending_earnings', 0.0) or 0.0
    total_withdrawn = getattr(current_user, 'total_withdrawn', 0.0) or 0.0

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request, "surveys": surveys_data,
            "completed_today": completed_today, "total_earned": pending_earnings,
            "total_withdrawn": total_withdrawn, "pending_earnings": pending_earnings,
            "available_surveys": len(surveys_data), "current_user": current_user,
            "stripe_onboarding_complete": getattr(current_user, 'stripe_onboarding_complete', 'false'),
        }
    )


@app.get("/my-studies", response_class=HTMLResponse)
def participant_my_studies(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    responses = db.query(Response).filter(
        Response.participant_id == current_user.id
    ).order_by(Response.started_at.desc()).all()

    surveys_data = []
    for response in responses:
        survey = db.query(Survey).filter(Survey.id == response.survey_id).first()
        if survey:
            surveys_data.append(_participant_survey_payload(survey, db, current_user, response))

    return templates.TemplateResponse("participant_my_studies.html", {
        "request": request,
        "current_user": current_user,
        "surveys": surveys_data,
        "pending_earnings": getattr(current_user, 'pending_earnings', 0.0) or 0.0,
    })


@app.get("/earnings", response_class=HTMLResponse)
def participant_earnings(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    responses = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed"
    ).order_by(Response.completed_at.desc()).all()

    surveys_data = []
    for response in responses:
        survey = db.query(Survey).filter(Survey.id == response.survey_id).first()
        if survey:
            surveys_data.append(_participant_survey_payload(survey, db, current_user, response))

    return templates.TemplateResponse("participant_earnings.html", {
        "request": request,
        "current_user": current_user,
        "surveys": surveys_data,
        "pending_earnings": getattr(current_user, 'pending_earnings', 0.0) or 0.0,
        "total_withdrawn": getattr(current_user, 'total_withdrawn', 0.0) or 0.0,
        "stripe_onboarding_complete": getattr(current_user, 'stripe_onboarding_complete', 'false'),
    })
# ---------------------------
# mobile dashboard (simplified for mobile users)
# ---------------------------
@app.get("/dashboard/mobile", response_class=HTMLResponse)
def dashboard_mobile(
    request: Request,
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    all_published = db.query(Survey).filter(Survey.status == "published").all()

    user_edu_min = _education_rank(current_user.education_level, fallback=0)
    user_edu_max = _education_rank(current_user.education_level, fallback=999)

    def survey_matches(s: Survey) -> bool:
        if not _field_matches(s.target_age_range, current_user.age_range): return False
        if s.target_education_min is not None:
            if user_edu_min < s.target_education_min: return False
        if s.target_education_max is not None:
            if user_edu_max > s.target_education_max: return False
        if not _field_matches(s.target_field, current_user.field): return False
        if not _field_matches(s.target_status, current_user.status): return False
        if not _location_matches(s.target_state, current_user): return False
        if not _language_matches(s.target_language, current_user.language): return False
        if not _field_matches(s.target_ethnicity, current_user.ethnicity): return False
        if not _field_matches(s.target_sexual_orientation, current_user.sexual_orientation): return False
        if not _field_matches(s.target_mental_health_diagnosis, current_user.mental_health_diagnosis): return False
        if not _field_matches(s.target_physical_health_diagnosis, current_user.physical_health_diagnosis): return False
        if not _field_matches(s.target_sport_type, current_user.sport_type): return False
        if not _field_matches(s.target_sport_frequency, current_user.sport_frequency): return False
        if not _field_matches(s.target_smoking, current_user.smoking): return False
        if not _field_matches(s.target_cannabis_use, current_user.cannabis_use): return False
        if not _field_matches(getattr(s, 'target_student_status', None), getattr(current_user, 'student_status', None)): return False
        if not _field_matches(getattr(s, 'target_international_domestic', None), getattr(current_user, 'international_domestic', None)): return False
        if not _tags_match(getattr(s, 'target_experience_tags', None), getattr(current_user, 'experience_tags', None)): return False
        if not _participation_format_matches(getattr(s, 'target_participation_format', None), getattr(current_user, 'participation_format', None)): return False
        if not _device_matches(getattr(s, 'target_device', None), getattr(current_user, 'device_type', None)): return False
        if not _field_matches(getattr(s, 'target_income_level', None), getattr(current_user, 'income_level', None)): return False
        if not _tags_match(getattr(s, 'target_lifestyle_tags', None), getattr(current_user, 'lifestyle_tags', None)): return False
        return True

    matched = [s for s in all_published if survey_matches(s)]

    # LLM-only recommendation ranking for mobile: Claude provides completion_probability.
    # Urgency/date are no longer used as the recommendation score.
    recommendation_map = recommend_surveys_for_user(db, matched, current_user, use_cache=True)
    matched.sort(
        key=lambda s: (
            float(recommendation_map.get(s.id, {}).get("completion_probability") or 0.0),
            s.published_at.timestamp() if s.published_at else 0,
        ),
        reverse=True,
    )

    category_images = {
        "research": "/static/psych.jpg", "life": "/static/campus_life.jpg",
        "clubs": "/static/fb.jpg", "market": "/static/habit.png",
        "academic": "/static/r2.jpg", "other": "/static/food.jpeg"
    }

    surveys_data = []
    for s in matched:
        llm_rec = recommendation_map.get(s.id, {})
        completed_cnt = db.query(Response).filter(
            Response.survey_id == s.id, Response.status == "completed"
        ).count()
        user_response = db.query(Response).filter(
            Response.survey_id == s.id, Response.participant_id == current_user.id
        ).first()
        is_completed = user_response and user_response.status == "completed"
        form_link = s.form_url if s.form_url and s.form_url != "__builtin__" else ""
        surveys_data.append({
            "id": s.id, "title": s.title, "desc": s.description,
            "link": form_link,
            "type": _normalize_task_type(getattr(s, "task_type", None)),
            "category": s.category, "time": f"{s.estimated_time} min",
            "reward": f"${s.reward_amount:.2f}",
            "responses": f"{completed_cnt}/{s.target_responses}",
            "img": s.image_url if s.image_url else category_images.get(s.category, "/static/psych.jpg"),
            "is_completed": is_completed,
            "urgency": getattr(s, 'urgency_level', None) or 'flexible',
            "incentive_type": getattr(s, 'incentive_type', None),
            "completion_probability": llm_rec.get("completion_probability"),
            "ai_confidence": llm_rec.get("confidence"),
            "why_recommended": (llm_rec.get("top_reasons") or [])[:3],
            "risk_reasons": (llm_rec.get("risk_reasons") or [])[:3],
            "recommended_action": llm_rec.get("recommended_action"),
            "ranking_note": llm_rec.get("ranking_note"),
            "model_version": llm_rec.get("model_version"),
        })

    pending_earnings = getattr(current_user, 'pending_earnings', 0.0) or 0.0
    total_withdrawn = getattr(current_user, 'total_withdrawn', 0.0) or 0.0

    completed_today = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
    ).count()

    return templates.TemplateResponse("dashboard_mobile.html", {
        "request": request,
        "surveys": surveys_data,
        "completed_today": completed_today,
        "pending_earnings": pending_earnings,
        "total_withdrawn": total_withdrawn,
        "available_surveys": len(surveys_data),
        "current_user": current_user,
        "stripe_onboarding_complete": getattr(current_user, 'stripe_onboarding_complete', 'false'),
        "researcher_desktop_url": BASE_URL,
        "researcher_desktop_host": BASE_URL.replace("https://", "").replace("http://", ""),
    })
# ---------------------------
# Dashboard stats API
# ---------------------------

@app.get("/api/dashboard/stats")
def get_dashboard_stats(
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if timezone_offset is not None:
        user_tz = timezone(timedelta(minutes=-timezone_offset))
        now_user = datetime.now(user_tz)
        today_start_user = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_user.astimezone(timezone.utc)
    else:
        now_utc = datetime.now(timezone.utc)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    completed_today = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= today_start_utc
    ).count()

    completed_responses = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None)
    ).all()

    total_earned = 0.0
    for resp in completed_responses:
        survey = db.query(Survey).filter(Survey.id == resp.survey_id).first()
        if survey:
            total_earned += survey.reward_amount

    return JSONResponse({"completed_today": completed_today, "total_earned": total_earned})


# ---------------------------
# Survey start / complete / modify
# ---------------------------

@app.post("/surveys/{survey_id}/start")
def start_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey: raise HTTPException(404, "Survey not found")
    if survey.status != "published": raise HTTPException(400, "Survey not published")

    existing = db.query(Response).filter(
        Response.survey_id == survey_id, Response.participant_id == current_user.id
    ).first()

    if not existing:
        db.add(Response(survey_id=survey_id, participant_id=current_user.id, status="started"))
        db.commit()

    return {"message": "Survey started successfully"}


@app.post("/surveys/{survey_id}/complete")
def complete_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey: raise HTTPException(404, "Survey not found")
    if survey.status != "published": raise HTTPException(400, "Survey not published")

    r = db.query(Response).filter(
        Response.survey_id == survey_id, Response.participant_id == current_user.id
    ).first()

    if not r:
        r = Response(survey_id=survey_id, participant_id=current_user.id, status="started")
        db.add(r)

    if r.status != "completed":
        r.status = "completed"
        r.completed_at = datetime.now(timezone.utc)
        r.payout_amount = survey.reward_amount
        mark_response_under_review(r)

        publisher = db.query(User).filter(User.id == survey.publisher_id).first()
        if publisher and publisher.email:
            send_email(
                to=publisher.email,
                subject=f"[Insighta] New response ready for review: {survey.title}",
                body=f"""
                <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a18;">
                  <h2 style="font-size: 22px; margin-bottom: 8px;">📋 Response Ready for Review</h2>
                  <p style="color: #8a8a82; margin-bottom: 24px;">A participant has completed your survey and is awaiting your approval.</p>
                  <div style="background: #f3f1ea; border-radius: 10px; padding: 20px 24px; margin-bottom: 24px;">
                    <div style="font-size: 13px; color: #8a8a82; margin-bottom: 4px;">Survey</div>
                    <div style="font-size: 17px; font-weight: 600; margin-bottom: 12px;">{survey.title}</div>
                    <div style="font-size: 13px; color: #8a8a82; margin-bottom: 4px;">Participant</div>
                    <div style="font-size: 15px;">{current_user.email}</div>
                    <div style="font-size: 13px; color: #8a8a82; margin-top: 12px; margin-bottom: 4px;">Reward</div>
                    <div style="font-size: 15px; font-weight: 600; color: #2d6a4f;">${survey.reward_amount:.2f}</div>
                  </div>
                  <a href="https://insightaco.org/publisher" style="display: inline-block; padding: 12px 24px; background: #2d6a4f; color: white; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px;">
                    Review & Approve →
                  </a>
                </div>
                """
            )

    db.commit()
    return RedirectResponse(url="/dashboard", status_code=302)


@app.post("/surveys/{survey_id}/modify")
def modify_completed_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    r = db.query(Response).filter(
        Response.survey_id == survey_id, Response.participant_id == current_user.id
    ).first()

    if r and r.status == "completed":
        survey = db.query(Survey).filter(Survey.id == survey_id).first()
        r.status = "started"; r.completed_at = None
        return_response_to_review(db, r)
        db.commit()
        return {"message": "Response modified"}

    return JSONResponse({"detail": "Response not found or not completed"}, status_code=404)


# ---------------------------
# Notifications API
# ---------------------------

@app.get("/api/notifications")
def get_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifs = db.query(Notification).filter(
        Notification.publisher_id == current_user.id
    ).order_by(Notification.created_at.desc()).all()
    return JSONResponse([{
        "id": n.id,
        "participant_email": n.participant_email,
        "survey_title": n.survey_title,
        "task_type": n.task_type or "survey",
        "status": n.status,
        "created_at": n.created_at.strftime("%b %d, %H:%M") if n.created_at else "",
    } for n in notifs])


@app.post("/api/notifications/{notif_id}/accept")
def accept_notification(
    notif_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notif = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.publisher_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(404, "Notification not found")
    notif.status = "accepted"

    response = db.query(Response).filter(
        Response.survey_id == notif.survey_id,
        Response.participant_id == notif.participant_id,
    ).first()
    if response:
        release_response_payout(db, response)
        survey = db.query(Survey).filter(Survey.id == notif.survey_id).first()
        participant = db.query(User).filter(User.id == notif.participant_id).first()
        if participant and survey:
            send_email(
                to=participant.email,
                subject="[Insighta] Your response was approved",
                body=f"""
                <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a18;">
                  <h2 style="font-size: 22px; margin-bottom: 8px;">Response Approved</h2>
                  <p style="color: #8a8a82; margin-bottom: 24px;">Your response has been verified and approved.</p>
                  <div style="background: #f3f1ea; border-radius: 10px; padding: 20px 24px; margin-bottom: 24px;">
                    <div style="font-size: 13px; color: #8a8a82; margin-bottom: 4px;">Survey</div>
                    <div style="font-size: 17px; font-weight: 600; margin-bottom: 12px;">{survey.title}</div>
                    <div style="font-size: 13px; color: #8a8a82; margin-bottom: 4px;">Reward</div>
                    <div style="font-size: 22px; font-weight: 700; color: #2d6a4f;">${survey.reward_amount:.2f}</div>
                  </div>
                </div>
                """,
            )
    db.commit()
    return {"message": "accepted"}


@app.post("/api/notifications/{notif_id}/reject")
def reject_notification(
    notif_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notif = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.publisher_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(404, "Notification not found")
    notif.status = "rejected"

    response = db.query(Response).filter(
        Response.survey_id == notif.survey_id,
        Response.participant_id == notif.participant_id,
    ).first()
    if response:
        reject_response_payout(db, response)
    db.commit()
    return {"message": "rejected"}


# ---------------------------
# Publisher get pending responses
# ---------------------------

@app.get("/api/publisher/pending-responses")
def get_pending_responses(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    surveys = db.query(Survey).filter(Survey.publisher_id == current_user.id).all()
    survey_ids = [s.id for s in surveys]
    survey_map = {s.id: s for s in surveys}
    if not survey_ids:
        return JSONResponse([])
    pending = db.query(Response).filter(
        Response.survey_id.in_(survey_ids), Response.status == "started"
    ).all()
    result = []
    for r in pending:
        participant = db.query(User).filter(User.id == r.participant_id).first()
        survey = survey_map.get(r.survey_id)
        quality = db.query(ResponseQualityCheck).filter(
            ResponseQualityCheck.response_id == r.id
        ).order_by(ResponseQualityCheck.created_at.desc()).first()
        result.append({
            "response_id": r.id, "survey_id": r.survey_id,
            "survey_title": survey.title if survey else "Unknown",
            "participant_email": participant.email if participant else "Unknown",
            "participant_name": participant.username or participant.email if participant else "Unknown",
            "reward": survey.reward_amount if survey else 0,
            "started_at": str(r.started_at),
            "quality_score": quality.quality_score if quality else None,
            "quality_label": quality.quality_label if quality else None,
            "fraud_risk": quality.fraud_risk if quality else None,
            "quality_reasons": quality.reasons if quality else [],
        })
    return JSONResponse(result)


# ---------------------------
# Publish survey page
# ---------------------------
@app.get("/publish", response_class=HTMLResponse)
def publish_page(
    request: Request,
    builtin: int = 0,
    survey_id: Optional[int] = None,
    title: Optional[str] = None,
    desc: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing_survey = None
    if survey_id:
        existing_survey = db.query(Survey).filter(
            Survey.id == survey_id,
            Survey.publisher_id == current_user.id,
        ).first()
    is_builtin = bool(builtin) or (
        existing_survey is not None and existing_survey.form_url == "__builtin__"
    )
    return templates.TemplateResponse("publish_external.html", {
        "request": request,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "builtin": is_builtin,
        "existing_survey_id": survey_id or 0,
        "prefill_title": title or (existing_survey.title if existing_survey else ""),
        "prefill_desc": desc or (existing_survey.description if existing_survey else ""),
        "existing_survey": existing_survey,
        "current_user": current_user
    })

# ---------------------------
# Calculate price API
# ---------------------------

@app.post("/api/calculate-price")
async def calculate_price(request: Request, current_user: User = Depends(get_current_user)):
    body = await request.json()
    per_person = body.get("per_person")
    total_budget = body.get("total_budget")
    target_responses = body.get("target_responses", 1)
    if per_person:
        ppg = float(per_person)
    elif total_budget and target_responses:
        ppg = float(total_budget) / int(target_responses)
    else:
        raise HTTPException(400, "Provide per_person or total_budget + target_responses")
    rate, reward = calculate_commission(ppg)
    total = round(ppg * int(target_responses), 2)
    return JSONResponse({
        "per_person_gross": round(ppg, 2), "commission_rate": rate,
        "commission_pct": int(rate * 100), "reward_amount": reward, "total_budget": total,
    })


# ---------------------------
# Publish survey → Stripe Checkout
# ---------------------------



# ---------------------------
# Publish interview
# ---------------------------

@app.get("/publish_interview", response_class=HTMLResponse)
def publish_interview_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("publish_interview.html", {"request": request, "current_user": current_user})

@app.post("/publish_interview")
async def publish_interview(
    request: Request, title: str = Form(...), description: str = Form(...),
    category: str = Form(...), estimated_time: int = Form(...), target_responses: int = Form(...),
    interview_format: str = Form("video"), scheduling_link: Optional[str] = Form(None),
    availability_notes: Optional[str] = Form(None), interview_location: Optional[str] = Form(None),
    urgency_level: Optional[str] = Form(None), deadline_date: Optional[str] = Form(None),
    incentive_type: Optional[str] = Form(None), per_person_gross: Optional[float] = Form(None),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    form = await request.form()
    experience_list = form.getlist("target_experience_tags")
    lifestyle_list = form.getlist("target_lifestyle_tags")
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))
    incentive_clean = _clean_target(incentive_type) or "cash"
    is_no_pay = incentive_clean in ("raffle", "volunteer")
    reward = 0.0 if is_no_pay else (per_person_gross or 0.0)

    survey = Survey(
        publisher_id=current_user.id, title=title, description=description,
        form_url=scheduling_link or "", task_type="interview", category=category,
        estimated_time=estimated_time, reward_amount=reward, per_person_gross=reward,
        total_budget=round(reward * target_responses, 2), commission_rate=0.0, payment_status="paid",
        target_responses=target_responses, urgency_level=_clean_target(urgency_level),
        incentive_type=incentive_clean,
        raffle_prize_type=_clean_target(form.get("raffle_prize_type")) if incentive_clean == "raffle" else None,
        target_age_range=_clean_target(form.get("target_age_range")),
        target_education_min=target_education_min,
        target_education_max=target_education_max,
        target_field=_clean_target(form.get("target_field")),
        target_status=_clean_target(form.get("target_status")),
        target_state=_clean_target(form.get("target_state")),
        target_language=_clean_target(form.get("target_language")),
        target_student_status=_clean_target(form.get("target_student_status")),
        target_year_in_school=None,
        target_international_domestic=_clean_target(form.get("target_international_domestic")),
        target_experience_tags=",".join(experience_list) if experience_list else None,
        target_participation_format=_clean_target(form.get("target_participation_format")),
        target_device=_clean_target(form.get("target_device")),
        target_income_level=_clean_target(form.get("target_income_level")),
        target_lifestyle_tags=",".join(lifestyle_list) if lifestyle_list else None,
        target_niche_requirements=_clean_target(form.get("target_niche_requirements")),
        status="draft", published_at=None, closed_at=None,
    )
    db.add(survey); db.commit()
    return RedirectResponse("/publisher", status_code=303)


# ---------------------------
# Payment success
# ---------------------------

@app.get("/payment/success", response_class=HTMLResponse)
def payment_success(
    request: Request,
    survey_id: int = Query(None),
    user_id: str = Cookie(None),
    db: Session = Depends(get_db)
):
    current_user = None
    if user_id:
        try:
            current_user = db.query(User).filter(User.id == int(user_id)).first()
        except:
            pass
    survey = db.query(Survey).filter(Survey.id == survey_id).first() if survey_id else None

    # Fallback: mark paid and publish when landing on payment success page
    if survey and survey.payment_status != "paid":
        survey.payment_status = "paid"
        survey.status = "published"
        survey.published_at = datetime.utcnow()
        db.commit()

    return templates.TemplateResponse("payment_success.html", {
        "request": request, "survey": survey, "current_user": current_user,
    })

# ---------------------------
# Stripe Webhook
# ---------------------------

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(400, str(e))

    if event["type"] == "checkout.session.completed":
        survey_id = event["data"]["object"].get("metadata", {}).get("survey_id")
        if survey_id:
            survey = db.query(Survey).filter(Survey.id == int(survey_id)).first()
            if survey:
                survey.payment_status = "paid"
                survey.status = "published"
                survey.published_at = datetime.utcnow()
                db.commit()
    elif event["type"] == "account.updated":
        account = event["data"]["object"]
        if account.get("charges_enabled"):
            user = db.query(User).filter(User.stripe_account_id == account.get("id")).first()
            if user:
                user.stripe_onboarding_complete = "true"
                db.commit()

    return JSONResponse({"status": "ok"})


# ---------------------------
# Stripe Connect
# ---------------------------

@app.get("/connect/onboard")
def connect_onboard(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not getattr(current_user, 'stripe_account_id', None):
        account = stripe.Account.create(
            type="express",
            email=current_user.email,
            business_type="individual",
            individual={"email": current_user.email},
            capabilities={"transfers": {"requested": True}}
        )
        current_user.stripe_account_id = account.id; db.commit()
    account_link = stripe.AccountLink.create(
        account=current_user.stripe_account_id,
        refresh_url="https://insightaco.org/connect/onboard",
        return_url="https://insightaco.org/connect/complete",
        type="account_onboarding",
    )
    return RedirectResponse(account_link.url)
@app.get("/logout")
def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    policy = _cookie_policy(request)
    response.delete_cookie("user_id", samesite=policy["samesite"], secure=policy["secure"])
    return response

@app.get("/connect/complete", response_class=HTMLResponse)
def connect_complete(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if getattr(current_user, 'stripe_account_id', None):
        account = stripe.Account.retrieve(current_user.stripe_account_id)
        if account.charges_enabled:
            current_user.stripe_onboarding_complete = "true"
            db.commit()
    return RedirectResponse("/dashboard")


# ---------------------------
# Participant withdrawal
# ---------------------------

@app.post("/api/withdraw")
async def withdraw(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if getattr(current_user, 'stripe_onboarding_complete', 'false') != 'true':
        raise HTTPException(400, "Please complete Stripe onboarding first")
    pending = getattr(current_user, 'pending_earnings', 0.0) or 0.0
    if pending < 1.0: raise HTTPException(400, "Minimum withdrawal is $1.00")
    try:
        transfer = stripe.Transfer.create(amount=int(pending * 100), currency="usd", destination=current_user.stripe_account_id, description=f"Insighta payout for user {current_user.id}")
        for r in db.query(Response).filter(
            Response.participant_id == current_user.id,
            Response.payout_status.in_([APPROVED, LEGACY_RELEASED]),
        ).all():
            r.payout_status = "paid"; r.stripe_transfer_id = transfer.id
        current_user.total_withdrawn = (getattr(current_user, 'total_withdrawn', 0.0) or 0.0) + pending
        current_user.pending_earnings = 0.0; db.commit()
        return JSONResponse({"success": True, "amount": pending, "transfer_id": transfer.id})
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))


# ---------------------------
# Survey status management
# ---------------------------

@app.post("/surveys/{survey_id}/publish")
def publish_existing_survey(survey_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.query(Survey).filter(Survey.id == survey_id, Survey.publisher_id == current_user.id).first()
    if not s: raise HTTPException(404, "Survey not found")
    if getattr(s, 'payment_status', 'unpaid') != 'paid': raise HTTPException(400, "Survey must be paid before publishing")
    if s.status != "published": s.status = "published"; s.published_at = datetime.utcnow(); s.closed_at = None
    db.commit()
    return RedirectResponse("/publisher", status_code=303)

@app.post("/surveys/{survey_id}/close")
def close_existing_survey(survey_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.query(Survey).filter(Survey.id == survey_id, Survey.publisher_id == current_user.id).first()
    if not s: raise HTTPException(404, "Survey not found")
    if s.status != "closed": s.status = "closed"; s.closed_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/publisher", status_code=303)

@app.post("/surveys/{survey_id}/reopen")
def reopen_closed_survey(survey_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.query(Survey).filter(Survey.id == survey_id, Survey.publisher_id == current_user.id).first()
    if not s: raise HTTPException(404, "Survey not found")
    if s.status == "closed": s.status = "published"; s.closed_at = None
    db.commit()
    return RedirectResponse("/publisher", status_code=303)


# ---------------------------
# Edit survey
# ---------------------------

@app.get("/publisher/edit/{survey_id}")
def edit_survey_get(
    request: Request,
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    current_responses = db.query(Response).filter(Response.survey_id == survey_id, Response.status == "completed").count()
    survey.current_responses = current_responses
    return templates.TemplateResponse("edit_publish.html", {
        "request": request,
        "survey": survey,
        "current_user": current_user,
    })

@app.post("/publisher/edit/{survey_id}")
async def edit_survey_post(
    request: Request, survey_id: int,
    title: str = Form(...), description: str = Form(...), form_url: str = Form(...),
    task_type: str = Form("survey"), category: str = Form(...),
    estimated_time: int = Form(...), reward_amount: float = Form(...), additional_needed: int = Form(...),
    urgency_level: str = Form(None), incentive_type: str = Form(None),
    target_age_range: str = Form(None), target_field: str = Form(None),
    target_status: str = Form(None), target_state: str = Form(None),
    target_language: str = Form(None), target_ethnicity: str = Form(None),
    target_sexual_orientation: str = Form(None), target_mental_health_diagnosis: str = Form(None),
    target_physical_health_diagnosis: str = Form(None), target_sport_type: str = Form(None),
    target_sport_frequency: str = Form(None), target_smoking: str = Form(None),
    target_cannabis_use: str = Form(None), target_student_status: str = Form(None),
    target_year_in_school: str = Form(None), target_international_domestic: str = Form(None),
    target_participation_format: str = Form(None), target_device: str = Form(None),
    target_income_level: str = Form(None), raffle_prize_type: str = Form(None),
    cover_image: UploadFile = File(None),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    form = await request.form()
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))
    experience_list = form.getlist("target_experience_tags")
    target_experience_tags = ",".join(experience_list) if experience_list else None
    lifestyle_list = form.getlist("target_lifestyle_tags")
    target_lifestyle_tags = ",".join(lifestyle_list) if lifestyle_list else None

    survey = db.query(Survey).filter(Survey.id == survey_id, Survey.publisher_id == current_user.id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    if survey.status == "published":
        reward_amount = survey.reward_amount

    current_responses = db.query(Response).filter(Response.survey_id == survey_id, Response.status == "completed").count()

    # Update all survey fields
    survey.title = title; survey.description = description; survey.form_url = form_url
    survey.task_type = task_type; survey.category = category
    survey.estimated_time = estimated_time; survey.reward_amount = reward_amount
    survey.target_responses = current_responses + additional_needed
    survey.urgency_level = _clean_target(urgency_level); survey.incentive_type = _clean_target(incentive_type)
    survey.raffle_prize_type = _clean_target(raffle_prize_type) if _clean_target(incentive_type) == "raffle" else None
    survey.target_age_range = _clean_target(target_age_range)
    survey.target_education_min = target_education_min; survey.target_education_max = target_education_max
    survey.target_field = _clean_target(target_field); survey.target_status = _clean_target(target_status)
    survey.target_state = _clean_target(target_state); survey.target_language = _clean_target(target_language)
    survey.target_ethnicity = _clean_target(target_ethnicity)
    survey.target_sexual_orientation = _clean_target(target_sexual_orientation)
    survey.target_mental_health_diagnosis = _clean_target(target_mental_health_diagnosis)
    survey.target_physical_health_diagnosis = _clean_target(target_physical_health_diagnosis)
    survey.target_sport_type = _clean_target(target_sport_type)
    survey.target_sport_frequency = _clean_target(target_sport_frequency)
    survey.target_smoking = _clean_target(target_smoking); survey.target_cannabis_use = _clean_target(target_cannabis_use)
    survey.target_student_status = _clean_target(target_student_status)
    survey.target_year_in_school = _clean_target(target_year_in_school)
    survey.target_international_domestic = _clean_target(target_international_domestic)
    survey.target_experience_tags = target_experience_tags
    survey.target_participation_format = _clean_target(target_participation_format)
    survey.target_device = _clean_target(target_device)
    survey.target_income_level = _clean_target(target_income_level)
    survey.target_lifestyle_tags = target_lifestyle_tags
    survey.target_niche_requirements = _clean_target(form.get("target_niche_requirements"))
    _apply_survey_auto_filter_settings(survey, form)

    if cover_image and cover_image.filename:
        uploads_dir = Path("app/static/uploads"); uploads_dir.mkdir(exist_ok=True)
        unique_filename = f"{uuid.uuid4()}{Path(cover_image.filename).suffix}"
        file_path = uploads_dir / unique_filename
        with file_path.open("wb") as buffer: shutil.copyfileobj(cover_image.file, buffer)
        survey.image_url = f"/static/uploads/{unique_filename}"

    db.commit()

    # Published survey with additional responses that require payment
    is_no_pay = _clean_target(incentive_type) in ("raffle", "volunteer")
    if survey.status == "published" and additional_needed > 0 and not is_no_pay:
        total = round(reward_amount * additional_needed, 2)
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Additional responses: {survey.title}",
                        "description": f"{additional_needed} more responses × ${reward_amount:.2f} per person"
                    },
                    "unit_amount": int(round(total * 100))
                },
                "quantity": 1
            }],
            mode="payment",
            success_url=f"https://insightaco.org/payment/success?survey_id={survey.id}",
            cancel_url=f"https://insightaco.org/publisher",
            metadata={"survey_id": str(survey.id), "publisher_id": str(current_user.id)}
        )
        return RedirectResponse(session.url, status_code=303)

    return RedirectResponse("/publisher", status_code=303)
# ---------------------------
# Profile
# ---------------------------

@app.get("/profile", response_class=HTMLResponse)
def profile_get(
    request: Request,
    participant_app: Optional[str] = Cookie(None),
    current_user: User = Depends(get_current_user)
):
    if _should_use_participant_app(request, participant_app):
        return templates.TemplateResponse("participant_profile.html", {
            "request": request,
            "current_user": current_user,
            "pending_earnings": getattr(current_user, 'pending_earnings', 0.0) or 0.0,
        })
    prev_url = request.headers.get("referer", "/publisher")
    return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "prev_url": prev_url})

@app.get("/profile/edit", response_class=HTMLResponse)
def profile_edit_get(request: Request, current_user: User = Depends(get_current_user)):
    prev_url = request.headers.get("referer", "/profile")
    return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "prev_url": prev_url})

@app.post("/profile")
async def profile_post(
    request: Request,
    participant_app: Optional[str] = Cookie(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    form = await request.form()
    if not _should_use_participant_app(request, participant_app):
        current_user.username = (form.get("username") or "").strip() or None
        current_user.email = (form.get("email") or "").strip()
        if "phone_number" in form:
            current_user.phone_number = (form.get("phone_number") or "").strip() or None
        current_user.field = _value_with_other(form.get("field"), form.get("field_other"))
        current_user.status = _value_with_other(form.get("status"), form.get("status_other"))
        current_user.profile_description = (form.get("profile_description") or "").strip() or None
        db.commit()
        return_to = form.get("return_to")
        if return_to and is_safe_internal_next(return_to):
            return RedirectResponse(return_to, status_code=303)
        return RedirectResponse("/publisher", status_code=303)

    language_list = _list_with_other(form.getlist("language"), form.get("language_other"))
    experience_list = _list_with_other(form.getlist("experience_tags"), form.get("experience_tags_other"))
    lifestyle_list = _list_with_other(form.getlist("lifestyle_tags"), form.get("lifestyle_tags_other"))

    current_user.username = form.get("username")
    current_user.email = form.get("email") or ""
    if "phone_number" in form:
        current_user.phone_number = (form.get("phone_number") or "").strip() or None

    birth_year = form.get("birth_year")
    birth_month = form.get("birth_month")
    derived_age_range = _age_range_from_birth_date(birth_year, birth_month)
    current_user.birth_year = birth_year or None
    current_user.birth_month = birth_month or None
    current_user.age_range = derived_age_range or form.get("age_range")
    current_user.profile_description = form.get("profile_description")

    current_user.education_level = form.get("education_level")
    current_user.field = _value_with_other(form.get("field"), form.get("field_other"))
    current_user.status = _value_with_other(form.get("status"), form.get("status_other"))

    current_user.current_country = form.get("current_country")
    current_user.current_province = form.get("current_province")
    current_user.current_city = form.get("current_city")
    current_user.origin_country = form.get("origin_country")
    current_user.origin_province = form.get("origin_province")
    current_user.origin_city = form.get("origin_city")
    current_user.state = form.get("state") or form.get("current_province") or form.get("current_country")

    current_user.race = _value_with_other(form.get("race"), form.get("race_other"))
    current_user.ethnicity = form.get("ethnicity") or current_user.race
    current_user.mental_health_diagnosis = _value_with_other(
        form.get("mental_health_diagnosis"),
        form.get("mental_health_diagnosis_other"),
    )
    current_user.physical_health_diagnosis = _value_with_other(
        form.get("physical_health_diagnosis"),
        form.get("physical_health_diagnosis_other"),
    )
    current_user.sexual_orientation = form.get("sexual_orientation")
    current_user.sport_type = form.get("sport_type"); current_user.sport_frequency = form.get("sport_frequency")
    current_user.smoking = form.get("smoking"); current_user.cannabis_use = form.get("cannabis_use")
    current_user.language = ",".join(language_list) if language_list else None
    current_user.student_status = _value_with_other(form.get("student_status"), form.get("student_status_other"))
    current_user.year_in_school = _value_with_other(form.get("year_in_school"), form.get("year_in_school_other"))
    current_user.international_domestic = form.get("international_domestic")
    current_user.experience_tags = ",".join(experience_list) if experience_list else None
    current_user.income_level = form.get("income_level")
    current_user.lifestyle_tags = ",".join(lifestyle_list) if lifestyle_list else None
    current_user.participation_format = _value_with_other(form.get("participation_format"), form.get("participation_format_other"))
    current_user.device_type = _value_with_other(form.get("device_type"), form.get("device_type_other"))
    db.commit()
    return_to = form.get("return_to")
    if return_to and is_safe_internal_next(return_to):
        return RedirectResponse(return_to, status_code=303)
    if _should_use_participant_app(request, participant_app):
        return RedirectResponse("/profile", status_code=303)
    return RedirectResponse("/publisher", status_code=303)


# ---------------------------
# AI Fill
# ---------------------------

@app.post("/api/ai-fill")
async def ai_fill(request: Request, current_user: User = Depends(get_current_user)):
    try:
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt: raise HTTPException(400, "Prompt is required")
        import anthropic, json as _json
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1000,
            messages=[{"role": "user", "content": f"""You are helping a researcher fill out a survey publishing form.
Based on this description: "{prompt}"
Return ONLY a valid JSON object with these exact fields, no extra text:
{{"title": "clear survey title under 10 words", "description": "2-3 sentence description", "category": "one of: research, life, clubs, market, academic, other", "estimated_time": 5, "per_person_gross": 5.00, "target_responses": 100}}"""}]
        )
        text = message.content[0].text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match: raise HTTPException(500, "AI response parsing failed")
        result = _json.loads(match.group())
        return JSONResponse(result)
    except Exception as e:
        import traceback; print(traceback.format_exc())
        raise HTTPException(500, str(e))


# ---------------------------
# AI Generate Questions
# ---------------------------

@app.post("/api/ai-generate-questions")
async def ai_generate_questions(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    try:
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            raise HTTPException(400, "Prompt is required")

        import anthropic, json as _json
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"""You are helping a researcher design a survey.
Based on this research goal: "{prompt}"

Generate 6-8 survey questions. Return ONLY a valid JSON array, no extra text:
[
  {{
    "question_text": "What is your current year in school?",
    "question_type": "single",
    "options": ["Freshman", "Sophomore", "Junior", "Senior", "Graduate"],
    "is_required": true,
    "order_index": 1
  }},
  {{
    "question_text": "How many hours do you sleep on average per night?",
    "question_type": "scale",
    "options": null,
    "is_required": true,
    "order_index": 2
  }},
  {{
    "question_text": "Describe any sleep difficulties you experience.",
    "question_type": "text",
    "options": null,
    "is_required": false,
    "order_index": 3
  }}
]

Rules:
- Mix question types: single/multiple for closed, scale for ratings, text for open-ended
- Keep questions neutral, avoid leading language
- scale and text questions must have options: null
- options field is only for single, multiple, dropdown types
"""
            }]
        )

        text = message.content[0].text
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            raise HTTPException(500, "AI response parsing failed")

        questions = _json.loads(match.group())
        return JSONResponse({"questions": questions})

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(500, str(e))


# ---------------------------
# Feedback
# ---------------------------

@app.get("/feedback", response_class=HTMLResponse)
def feedback_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("feedback.html", {"request": request, "current_user": current_user})

@app.post("/feedback")
async def submit_feedback(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    form = await request.form()
    db.add(Feedback(user_id=current_user.id, category=form.get("category", "general"), title=form.get("title", ""), content=form.get("content", ""), status="pending"))
    db.commit()
    return RedirectResponse("/feedback?success=1", status_code=303)


# ---------------------------
# Support chat
# ---------------------------

SUPPORT_AVAILABLE_HOURS = "10am-5pm"

def _support_thread_payload(thread: SupportThread, db: Session):
    user = db.query(User).filter(User.id == thread.user_id).first()
    last_message = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id
    ).order_by(SupportMessage.created_at.desc()).first()
    unread_user_messages = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id,
        SupportMessage.sender_type == "user",
        SupportMessage.read_at.is_(None),
    ).count()
    return {
        "id": thread.id,
        "status": thread.status,
        "user_id": thread.user_id,
        "user_email": user.email if user else "unknown",
        "user_name": user.username if user else None,
        "last_message": last_message.body if last_message else "",
        "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "unread_user_messages": unread_user_messages,
    }

def _support_message_payload(message: SupportMessage):
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "sender_type": message.sender_type,
        "sender_id": message.sender_id,
        "body": message.body,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }

def _get_or_create_support_thread(db: Session, user_id: int):
    thread = db.query(SupportThread).filter(
        SupportThread.user_id == user_id,
        SupportThread.status == "open",
    ).order_by(SupportThread.updated_at.desc()).first()
    if thread:
        return thread
    thread = SupportThread(user_id=user_id, status="open", last_message_at=datetime.utcnow())
    db.add(thread)
    db.flush()
    db.add(SupportMessage(
        thread_id=thread.id,
        sender_type="system",
        body=f"Support hours are {SUPPORT_AVAILABLE_HOURS}. Leave a message and our team will reply here.",
    ))
    db.commit()
    db.refresh(thread)
    return thread

@app.get("/api/support/availability")
def support_availability():
    return JSONResponse({
        "available_hours": SUPPORT_AVAILABLE_HOURS,
        "label": f"Human support available {SUPPORT_AVAILABLE_HOURS}",
    })

@app.get("/api/support/thread")
def get_support_thread(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    thread = _get_or_create_support_thread(db, current_user.id)
    messages = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id
    ).order_by(SupportMessage.created_at.asc()).all()
    return JSONResponse({
        "thread": _support_thread_payload(thread, db),
        "messages": [_support_message_payload(m) for m in messages],
        "available_hours": SUPPORT_AVAILABLE_HOURS,
    })

@app.get("/api/support/messages")
def get_support_messages(
    thread_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    thread = db.query(SupportThread).filter(
        SupportThread.id == thread_id,
        SupportThread.user_id == current_user.id,
    ).first()
    if not thread:
        raise HTTPException(404, "Support thread not found")
    messages = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id
    ).order_by(SupportMessage.created_at.asc()).all()
    return JSONResponse([_support_message_payload(m) for m in messages])

@app.post("/api/support/messages")
async def send_support_message(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = await request.json()
    text_body = (body.get("body") or "").strip()
    if not text_body:
        raise HTTPException(400, "Message cannot be empty")
    if len(text_body) > 2000:
        raise HTTPException(400, "Message is too long")

    thread_id = body.get("thread_id")
    thread = None
    if thread_id:
        thread = db.query(SupportThread).filter(
            SupportThread.id == int(thread_id),
            SupportThread.user_id == current_user.id,
        ).first()
    if not thread:
        thread = _get_or_create_support_thread(db, current_user.id)

    msg = SupportMessage(
        thread_id=thread.id,
        sender_type="user",
        sender_id=current_user.id,
        body=text_body,
    )
    thread.status = "open"
    thread.last_message_at = datetime.utcnow()
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return JSONResponse({
        "thread": _support_thread_payload(thread, db),
        "message": _support_message_payload(msg),
    })


# ---------------------------
# Admin
# ---------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

@app.post("/admin/feedback/{feedback_id}/credit")
async def grant_credit(feedback_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    if body.get("admin_key", "") != os.environ.get("ADMIN_KEY", "insighta-admin"): raise HTTPException(403, "Unauthorized")
    amount = float(body.get("amount", 0))
    if amount <= 0: raise HTTPException(400, "Amount must be > 0")
    feedback = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not feedback: raise HTTPException(404, "Feedback not found")
    user = db.query(User).filter(User.id == feedback.user_id).first()
    if not user: raise HTTPException(404, "User not found")
    user.pending_earnings = (getattr(user, 'pending_earnings', 0.0) or 0.0) + amount
    feedback.status = "credited"; feedback.credit_amount = amount; feedback.reviewed_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"success": True, "user_email": user.email, "amount": amount, "new_balance": user.pending_earnings})

@app.post("/admin/feedback/{feedback_id}/reject")
async def reject_feedback(feedback_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    if body.get("admin_key") != os.environ.get("ADMIN_KEY", "insighta-admin"): raise HTTPException(403, "Unauthorized")
    feedback = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not feedback: raise HTTPException(404, "Feedback not found")
    feedback.status = "rejected"; feedback.reviewed_at = datetime.utcnow(); db.commit()
    return JSONResponse({"success": True})

@app.get("/admin/feedbacks")
async def list_feedbacks(request: Request, admin_key: str = Query(None), db: Session = Depends(get_db)):
    if admin_key != os.environ.get("ADMIN_KEY", "insighta-admin"): raise HTTPException(403, "Unauthorized")
    feedbacks = db.query(Feedback).order_by(Feedback.created_at.desc()).all()
    result = []
    for f in feedbacks:
        user = db.query(User).filter(User.id == f.user_id).first()
        result.append({"id": f.id, "user_email": user.email if user else "unknown", "category": f.category, "title": f.title, "content": f.content, "status": f.status, "credit_amount": f.credit_amount, "created_at": str(f.created_at)})
    return JSONResponse(result)

@app.get("/admin/support/threads")
async def admin_support_threads(admin_key: str = Query(None), db: Session = Depends(get_db)):
    if admin_key != os.environ.get("ADMIN_KEY", "insighta-admin"): raise HTTPException(403, "Unauthorized")
    threads = db.query(SupportThread).order_by(SupportThread.last_message_at.desc()).all()
    return JSONResponse([_support_thread_payload(thread, db) for thread in threads])

@app.get("/admin/support/threads/{thread_id}/messages")
async def admin_support_messages(thread_id: int, admin_key: str = Query(None), db: Session = Depends(get_db)):
    if admin_key != os.environ.get("ADMIN_KEY", "insighta-admin"): raise HTTPException(403, "Unauthorized")
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    if not thread:
        raise HTTPException(404, "Support thread not found")
    messages = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id
    ).order_by(SupportMessage.created_at.asc()).all()
    now = datetime.utcnow()
    for msg in messages:
        if msg.sender_type == "user" and msg.read_at is None:
            msg.read_at = now
    db.commit()
    return JSONResponse({
        "thread": _support_thread_payload(thread, db),
        "messages": [_support_message_payload(m) for m in messages],
        "available_hours": SUPPORT_AVAILABLE_HOURS,
    })

@app.post("/admin/support/threads/{thread_id}/messages")
async def admin_send_support_message(thread_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    if body.get("admin_key") != os.environ.get("ADMIN_KEY", "insighta-admin"): raise HTTPException(403, "Unauthorized")
    text_body = (body.get("body") or "").strip()
    if not text_body:
        raise HTTPException(400, "Message cannot be empty")
    if len(text_body) > 2000:
        raise HTTPException(400, "Message is too long")
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    if not thread:
        raise HTTPException(404, "Support thread not found")
    msg = SupportMessage(thread_id=thread.id, sender_type="admin", body=text_body)
    thread.status = "open"
    thread.last_message_at = datetime.utcnow()
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return JSONResponse({"message": _support_message_payload(msg), "thread": _support_thread_payload(thread, db)})

@app.post("/admin/support/threads/{thread_id}/status")
async def admin_update_support_status(thread_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    if body.get("admin_key") != os.environ.get("ADMIN_KEY", "insighta-admin"): raise HTTPException(403, "Unauthorized")
    status = (body.get("status") or "").strip().lower()
    if status not in {"open", "closed"}:
        raise HTTPException(400, "Unsupported status")
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    if not thread:
        raise HTTPException(404, "Support thread not found")
    thread.status = status
    thread.updated_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"thread": _support_thread_payload(thread, db)})

# ---------------------------
# Quick create built-in survey
# ---------------------------

@app.post("/surveys/create-builtin")
def create_builtin_survey(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = Survey(
        publisher_id=current_user.id,
        title="Untitled Survey",
        description="",
        form_url="__builtin__",
        task_type="survey",
        category="research",
        estimated_time=10,
        reward_amount=0.0,
        per_person_gross=0.0,
        total_budget=0.0,
        commission_rate=0.15,
        payment_status="unpaid",
        target_responses=50,
        status="draft",
    )
    db.add(survey)
    db.commit()
    db.refresh(survey)
    return RedirectResponse(f"/surveys/{survey.id}/builder", status_code=303)

@app.post("/publish")
async def publish_survey(
    request: Request,
    title: str = Form(...), description: str = Form(...), form_url: str = Form(...),
    task_type: str = Form("survey"), category: str = Form(...), estimated_time: int = Form(...),
    per_person_gross: Optional[float] = Form(None), total_budget: Optional[float] = Form(None),
    target_responses: int = Form(...), urgency_level: str = Form(None), incentive_type: str = Form(None),
    target_age_range: str = Form(None), target_field: str = Form(None), target_status: str = Form(None),
    target_state: str = Form(None), target_language: str = Form(None), target_ethnicity: str = Form(None),
    target_sexual_orientation: str = Form(None), target_mental_health_diagnosis: str = Form(None),
    target_physical_health_diagnosis: str = Form(None), target_sport_type: str = Form(None),
    target_sport_frequency: str = Form(None), target_smoking: str = Form(None),
    target_cannabis_use: str = Form(None), target_student_status: str = Form(None),
    target_year_in_school: str = Form(None), target_international_domestic: str = Form(None),
    target_participation_format: str = Form(None), target_device: str = Form(None),
    target_income_level: str = Form(None), raffle_prize_type: str = Form(None),
    cover_image: UploadFile = File(None),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    form = await request.form()
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))
    experience_list = form.getlist("target_experience_tags")
    target_experience_tags = ",".join(experience_list) if experience_list else None
    lifestyle_list = form.getlist("target_lifestyle_tags")
    target_lifestyle_tags = ",".join(lifestyle_list) if lifestyle_list else None
    target_niche_requirements = _clean_target(form.get("target_niche_requirements"))
    existing_survey_id = int(form.get("existing_survey_id") or 0)

    is_builtin = (form_url == "__builtin__")
    incentive_clean = _clean_target(incentive_type) or "cash"
    raffle_clean = _clean_target(raffle_prize_type)
    is_raffle = incentive_clean == "raffle"
    is_volunteer = incentive_clean == "volunteer"
    is_no_pay = is_raffle

    if is_raffle:
        ppg = 0.0; rate = 0.0; reward = 0.0; total = 0.0
    elif is_volunteer:
        ppg = 0.0; rate = 0.0; reward = 0.0
        total = volunteer_platform_fee(target_responses)
    else:
        if per_person_gross: ppg = float(per_person_gross)
        elif total_budget: ppg = float(total_budget) / int(target_responses)
        else: ppg = 5.0
        rate, reward = calculate_commission(ppg)
        total = round(ppg * int(target_responses), 2)
    total = round(total * timeline_multiplier(urgency_level), 2)

    image_url = None
    if cover_image and cover_image.filename:
        uploads_dir = Path("app/static/uploads"); uploads_dir.mkdir(exist_ok=True)
        unique_filename = f"{uuid.uuid4()}{Path(cover_image.filename).suffix}"
        file_path = uploads_dir / unique_filename
        with file_path.open("wb") as buffer: shutil.copyfileobj(cover_image.file, buffer)
        image_url = f"/static/uploads/{unique_filename}"

    if existing_survey_id:
        survey = db.query(Survey).filter(
            Survey.id == existing_survey_id,
            Survey.publisher_id == current_user.id
        ).first()
        if not survey:
            raise HTTPException(404, "Survey not found")
        survey.title = title
        survey.description = description
        survey.category = category
        survey.estimated_time = estimated_time
        survey.reward_amount = reward
        survey.per_person_gross = ppg
        survey.total_budget = total
        survey.commission_rate = rate
        survey.payment_status = "unpaid" if not is_no_pay else "paid"
        survey.target_responses = target_responses
        survey.urgency_level = _clean_target(urgency_level)
        survey.incentive_type = incentive_clean
        survey.raffle_prize_type = raffle_clean if is_raffle else None
        survey.target_age_range = _clean_target(target_age_range)
        survey.target_education_min = target_education_min
        survey.target_education_max = target_education_max
        survey.target_field = _clean_target(target_field)
        survey.target_status = _clean_target(target_status)
        survey.target_state = _clean_target(target_state)
        survey.target_language = _clean_target(target_language)
        survey.target_ethnicity = _clean_target(target_ethnicity)
        survey.target_sexual_orientation = _clean_target(target_sexual_orientation)
        survey.target_mental_health_diagnosis = _clean_target(target_mental_health_diagnosis)
        survey.target_physical_health_diagnosis = _clean_target(target_physical_health_diagnosis)
        survey.target_sport_type = _clean_target(target_sport_type)
        survey.target_sport_frequency = _clean_target(target_sport_frequency)
        survey.target_smoking = _clean_target(target_smoking)
        survey.target_cannabis_use = _clean_target(target_cannabis_use)
        survey.target_student_status = _clean_target(target_student_status)
        survey.target_year_in_school = None
        survey.target_international_domestic = _clean_target(target_international_domestic)
        survey.target_experience_tags = target_experience_tags
        survey.target_participation_format = _clean_target(target_participation_format)
        survey.target_device = _clean_target(target_device)
        survey.target_income_level = _clean_target(target_income_level)
        survey.target_lifestyle_tags = target_lifestyle_tags
        survey.target_niche_requirements = target_niche_requirements
        _apply_survey_auto_filter_settings(survey, form)
        db.commit()
        db.refresh(survey)
    else:
        survey = Survey(
            publisher_id=current_user.id, title=title, description=description, form_url=form_url,
            task_type=task_type, category=category, estimated_time=estimated_time,
            reward_amount=reward, per_person_gross=ppg, total_budget=total, commission_rate=rate,
            payment_status="unpaid" if not is_no_pay else "paid",
            target_responses=target_responses, urgency_level=_clean_target(urgency_level),
            incentive_type=incentive_clean, raffle_prize_type=raffle_clean if is_raffle else None,
            target_age_range=_clean_target(target_age_range),
            target_education_min=target_education_min, target_education_max=target_education_max,
            target_field=_clean_target(target_field), target_status=_clean_target(target_status),
            target_state=_clean_target(target_state), target_language=_clean_target(target_language),
            target_ethnicity=_clean_target(target_ethnicity),
            target_sexual_orientation=_clean_target(target_sexual_orientation),
            target_mental_health_diagnosis=_clean_target(target_mental_health_diagnosis),
            target_physical_health_diagnosis=_clean_target(target_physical_health_diagnosis),
            target_sport_type=_clean_target(target_sport_type),
            target_sport_frequency=_clean_target(target_sport_frequency),
            target_smoking=_clean_target(target_smoking), target_cannabis_use=_clean_target(target_cannabis_use),
            target_student_status=_clean_target(target_student_status),
            target_year_in_school=None,
            target_international_domestic=_clean_target(target_international_domestic),
            target_experience_tags=target_experience_tags,
            target_participation_format=_clean_target(target_participation_format),
            target_device=_clean_target(target_device),
            target_income_level=_clean_target(target_income_level),
            target_lifestyle_tags=target_lifestyle_tags,
            target_niche_requirements=target_niche_requirements,
            image_url=image_url, status="draft", published_at=None, closed_at=None,
        )
        _apply_survey_auto_filter_settings(survey, form)
        db.add(survey); db.commit(); db.refresh(survey)

    # Handle no-pay incentives, missing Stripe key, and Stripe checkout
    if is_no_pay or not stripe.api_key:
        survey.status = "published"
        survey.published_at = datetime.utcnow()
        if not stripe.api_key and not is_no_pay:
            survey.payment_status = "paid"
        db.commit()
        return RedirectResponse("/publisher", status_code=303)

    success_url = f"https://insightaco.org/payment/success?survey_id={survey.id}"
    if is_volunteer:
        stripe_description = f"Volunteer recruitment fee: ${volunteer_platform_fee(target_responses):.2f} per 10 participants"
    else:
        stripe_description = f"{survey.target_responses} responses x ${reward:.2f} per person"
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price_data": {"currency": "usd", "product_data": {"name": f"Survey: {survey.title}", "description": stripe_description}, "unit_amount": int(round(total * 100))}, "quantity": 1}],
        mode="payment",
        success_url=success_url,
        cancel_url="https://insightaco.org/publisher",
        metadata={"survey_id": str(survey.id), "publisher_id": str(current_user.id)}
    )
    survey.stripe_payment_intent_id = session.id; db.commit()
    return RedirectResponse(session.url, status_code=303)

# ---------------------------
# Update survey info from builder
# ---------------------------

@app.post("/surveys/{survey_id}/update-info")
async def update_survey_info(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    body = await request.json()
    survey.title = body.get("title", survey.title)
    survey.description = body.get("description", survey.description)
    survey.category = body.get("category", survey.category)
    survey.estimated_time = body.get("estimated_time", survey.estimated_time)
    survey.per_person_gross = body.get("per_person_gross", survey.per_person_gross)
    survey.reward_amount = body.get("reward_amount", survey.reward_amount)
    survey.total_budget = body.get("total_budget", survey.total_budget)
    survey.commission_rate = body.get("commission_rate", survey.commission_rate)
    survey.target_responses = body.get("target_responses", survey.target_responses)
    if "quality_auto_filter_enabled" in body:
        survey.quality_auto_filter_enabled = bool(body.get("quality_auto_filter_enabled"))
    if "quality_auto_filter_min_score" in body:
        survey.quality_auto_filter_min_score = _parse_auto_approve_min_score(
            body.get("quality_auto_filter_min_score", 80)
        )
    db.commit()
    return JSONResponse({"message": "updated"})
# ---------------------------
# Questions API
# ---------------------------

@app.post("/surveys/{survey_id}/questions")
async def add_question(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    request_data = await request.json()

    max_order = db.query(func.max(Question.order_index)).filter(
        Question.survey_id == survey_id
    ).scalar() or 0

    q = Question(
        survey_id=survey_id,
        question_text=request_data.get("question_text", ""),
        question_type=request_data.get("question_type", "single"),
        options=request_data.get("options"),
        is_required=request_data.get("is_required", True),
        order_index=request_data.get("order_index", max_order + 1)
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return JSONResponse({
        "id": q.id,
        "survey_id": q.survey_id,
        "question_text": q.question_text,
        "question_type": q.question_type,
        "options": q.options,
        "is_required": q.is_required,
        "order_index": q.order_index
    })


@app.get("/surveys/{survey_id}/questions")
def get_questions(
    survey_id: int,
    db: Session = Depends(get_db)
):
    questions = db.query(Question).filter(
        Question.survey_id == survey_id
    ).order_by(Question.order_index).all()
    return JSONResponse([{
        "id": q.id,
        "question_text": q.question_text,
        "question_type": q.question_type,
        "options": q.options,
        "is_required": q.is_required,
        "order_index": q.order_index
    } for q in questions])


@app.put("/surveys/{survey_id}/questions/{question_id}")
async def update_question(
    survey_id: int,
    question_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    request_data = await request.json()

    q = db.query(Question).filter(
        Question.id == question_id,
        Question.survey_id == survey_id
    ).first()
    if not q:
        raise HTTPException(404, "Question not found")

    if "question_text" in request_data:
        q.question_text = request_data["question_text"]
    if "question_type" in request_data:
        q.question_type = request_data["question_type"]
    if "options" in request_data:
        q.options = request_data["options"]
    if "is_required" in request_data:
        q.is_required = request_data["is_required"]

    db.commit()
    return JSONResponse({"message": "updated"})


@app.delete("/surveys/{survey_id}/questions/{question_id}")
def delete_question(
    survey_id: int,
    question_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    q = db.query(Question).filter(
        Question.id == question_id,
        Question.survey_id == survey_id
    ).first()
    if not q:
        raise HTTPException(404, "Question not found")

    db.delete(q)
    db.commit()
    return JSONResponse({"message": "deleted"})


@app.post("/surveys/{survey_id}/questions/reorder")
async def reorder_questions(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    body = await request.json()
    for item in body:
        q = db.query(Question).filter(
            Question.id == item["id"],
            Question.survey_id == survey_id
        ).first()
        if q:
            q.order_index = item["order_index"]

    db.commit()
    return JSONResponse({"message": "reordered"})


# ---------------------------
# Survey Builder page
# ---------------------------

@app.get("/surveys/{survey_id}/builder", response_class=HTMLResponse)
def survey_builder(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    return templates.TemplateResponse("survey_builder.html", {
        "request": request,
        "survey": survey,
        "survey_id": survey_id,
        "current_user": current_user
    })


# ---------------------------
# Survey Take page
# ---------------------------

@app.get("/surveys/{survey_id}/take", response_class=HTMLResponse)
def survey_take(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.status == "published"
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    return templates.TemplateResponse("survey_take.html", {
        "request": request,
        "survey": survey,
        "current_user": current_user
    })


# ---------------------------
# Survey Results page (HTML)
# ---------------------------

@app.get("/surveys/{survey_id}/results", response_class=HTMLResponse)
def survey_results_page(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    return templates.TemplateResponse("survey_results.html", {
        "request": request,
        "survey": survey,
        "survey_id": survey_id,
        "current_user": current_user
    })


# ---------------------------
# Survey Results data (JSON) — used by survey_results.html
# ---------------------------

@app.get("/api/surveys/{survey_id}/results")
def get_survey_results(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    questions = db.query(Question).filter(
        Question.survey_id == survey_id
    ).order_by(Question.order_index).all()

    total_responses = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed"
    ).count()

    result = []
    for q in questions:
        answers = db.query(Answer).join(Response).filter(
            Response.survey_id == survey_id,
            Response.status == "completed",
            Answer.question_id == q.id
        ).all()

        if q.question_type in ("single", "multiple", "dropdown"):
            distribution = {}
            for a in answers:
                val = a.answer_value
                if isinstance(val, list):
                    for v in val:
                        distribution[v] = distribution.get(v, 0) + 1
                else:
                    distribution[str(val)] = distribution.get(str(val), 0) + 1
            result.append({
                "question_id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "total_answers": len(answers),
                "distribution": distribution
            })

        elif q.question_type == "scale":
            values = [a.answer_value for a in answers if a.answer_value is not None]
            avg = round(sum(values) / len(values), 2) if values else 0
            distribution = {str(i): values.count(i) for i in range(1, 6)}
            result.append({
                "question_id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "total_answers": len(answers),
                "average": avg,
                "distribution": distribution
            })

        elif q.question_type == "text":
            result.append({
                "question_id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "total_answers": len(answers),
                "responses": [a.answer_value for a in answers]
            })

    return JSONResponse({
        "survey_id": survey_id,
        "survey_title": survey.title,
        "total_responses": total_responses,
        "questions": result
    })


# ---------------------------
# AI Analyze survey
# ---------------------------

@app.post("/surveys/{survey_id}/analyze")
async def analyze_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    questions = db.query(Question).filter(
        Question.survey_id == survey_id
    ).order_by(Question.order_index).all()

    answers_data = []
    for q in questions:
        answers = db.query(Answer).join(Response).filter(
            Response.survey_id == survey_id,
            Response.status == "completed",
            Answer.question_id == q.id
        ).all()
        answers_data.append({
            "question": q.question_text,
            "type": q.question_type,
            "answers": [a.answer_value for a in answers]
        })

    total_responses = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed"
    ).count()

    import anthropic, json as _json
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(503, "AI analysis is not available right now.")
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": f"""You are analyzing survey results for a researcher.

Survey title: {survey.title}
Survey description: {survey.description}
Total responses: {total_responses}

Data:
{_json.dumps(answers_data, ensure_ascii=False, indent=2)}

Please provide:
1. Key findings (3-5 bullet points with actual numbers)
2. Notable patterns or trends
3. Text response themes (if any open-ended questions)
4. One recommendation for the researcher

Be concise and specific. Use the actual numbers from the data.
Write in the same language as the survey questions.
"""
        }]
    )

    return JSONResponse({
        "analysis": message.content[0].text,
        "generated_at": datetime.utcnow().isoformat()
    })


# ---------------------------
# Answers submit API
# ---------------------------

@app.post("/surveys/{survey_id}/submit")
async def submit_answers(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    if survey.status != "published":
        raise HTTPException(400, "Survey not published")

    body = await request.json()
    meta = _extract_client_meta(request, current_user)

    r = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.participant_id == current_user.id
    ).first()
    if not r:
        r = Response(
            survey_id=survey_id,
            participant_id=current_user.id,
            status="started",
            client_ip=meta["client_ip"],
            user_agent=meta["user_agent"],
            device_fingerprint=meta["device_fingerprint"],
        )
        db.add(r)
        db.flush()
    else:
        r.client_ip = meta["client_ip"]
        r.user_agent = meta["user_agent"]
        r.device_fingerprint = meta["device_fingerprint"]

    for item in body:
        existing = db.query(Answer).filter(
            Answer.response_id == r.id,
            Answer.question_id == item["question_id"]
        ).first()
        if existing:
            existing.answer_value = item["answer_value"]
        else:
            db.add(Answer(
                response_id=r.id,
                question_id=item["question_id"],
                answer_value=item["answer_value"]
            ))

    if r.status != "completed":
        r.status = "completed"
        r.completed_at = datetime.now(timezone.utc)
        r.payout_amount = survey.reward_amount
        mark_response_under_review(r)

        publisher = db.query(User).filter(User.id == survey.publisher_id).first()
        if publisher and publisher.email:
            send_email(
                to=publisher.email,
                subject=f"[Insighta] New response ready for review: {survey.title}",
                body=f"""
                <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a18;">
                  <h2 style="font-size: 22px; margin-bottom: 8px;">📋 Response Ready for Review</h2>
                  <p style="color: #8a8a82; margin-bottom: 24px;">A participant has completed your survey.</p>
                  <div style="background: #f3f1ea; border-radius: 10px; padding: 20px 24px; margin-bottom: 24px;">
                    <div style="font-size: 13px; color: #8a8a82; margin-bottom: 4px;">Survey</div>
                    <div style="font-size: 17px; font-weight: 600; margin-bottom: 12px;">{survey.title}</div>
                    <div style="font-size: 13px; color: #8a8a82; margin-bottom: 4px;">Participant</div>
                    <div style="font-size: 15px;">{current_user.email}</div>
                  </div>
                  <a href="https://insightaco.org/publisher" style="display: inline-block; padding: 12px 24px; background: #2d6a4f; color: white; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px;">
                    Review & Approve →
                  </a>
                </div>
                """
            )

    quality_payload = None
    try:
        quality_result = evaluate_builtin_response(db, survey_id=survey_id, response_id=r.id)
        upsert_builtin_quality_check(db, survey_id=survey_id, response_id=r.id, result=quality_result)
        quality_payload = {
            "score": quality_result.quality_score,
            "label": quality_result.quality_label,
            "fraud_risk": quality_result.fraud_risk,
            "reasons": quality_result.reasons,
        }
    except Exception as exc:
        print(f"Quality check failed for response {r.id}: {exc}")

    db.commit()
    mark_latest_jump_completed_for_response(db, r)
    body = {"message": "submitted successfully"}
    if quality_payload:
        body["quality"] = quality_payload
    return JSONResponse(body)


@app.get("/api/surveys/{survey_id}/quality-results")
def get_survey_quality_results(
    survey_id: int,
    scope: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    normalized_scope = (scope or "").strip().lower()
    if normalized_scope == "builtin":
        ensure_builtin_quality_checks(db, survey_id)

    rows = db.query(ResponseQualityCheck).filter(
        ResponseQualityCheck.survey_id == survey_id
    ).order_by(ResponseQualityCheck.created_at.desc()).all()

    if normalized_scope == "builtin":
        rows = [row for row in rows if row.source_type == "builtin"]
    elif normalized_scope == "excel":
        rows = _latest_excel_quality_rows([row for row in rows if row.source_type == "excel"])
    else:
        rows = _latest_excel_quality_rows(rows)

    payload = []
    for index, row in enumerate(rows):
        participant_email = None
        if row.response_id:
            resp = db.query(Response).filter(Response.id == row.response_id).first()
            if resp:
                participant = db.query(User).filter(User.id == resp.participant_id).first()
                participant_email = participant.email if participant else None
        row_label = participant_email or _quality_row_label(index)
        payload.append({
            "id": row.id,
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "row_label": row_label,
            "response_id": row.response_id,
            "participant_email": participant_email,
            "quality_score": row.quality_score,
            "quality_label": row.quality_label,
            "fraud_risk": row.fraud_risk,
            "rule_penalty": row.rule_penalty,
            "anomaly_score": row.anomaly_score,
            "semantic_risk": row.semantic_risk,
            "triggered_rules": row.triggered_rules or [],
            "reasons": row.reasons or [],
            "review_status": row.review_status,
            "reviewer_label": row.reviewer_label,
            "llm_result_json": row.llm_result_json,
            "notes": row.notes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return JSONResponse(payload)


@app.get("/api/quality-checks/{check_id}")
def get_quality_check_detail(
    check_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row, survey = _get_quality_check_for_publisher(db, check_id, current_user.id)

    participant_email = None
    participant_name = None
    response_meta = None
    answers_payload = []
    submission_history = []

    if row.response_id:
        resp = db.query(Response).filter(Response.id == row.response_id).first()
        if resp:
            participant = db.query(User).filter(User.id == resp.participant_id).first()
            participant_email = participant.email if participant else None
            participant_name = (participant.username or participant.email) if participant else None
            response_meta = {
                "client_ip": resp.client_ip,
                "user_agent": resp.user_agent,
                "device_fingerprint": resp.device_fingerprint,
                "started_at": resp.started_at.isoformat() if resp.started_at else None,
                "completed_at": resp.completed_at.isoformat() if resp.completed_at else None,
            }
            answers = db.query(Answer).filter(Answer.response_id == resp.id).all()
            q_map = {
                q.id: q for q in db.query(Question).filter(Question.survey_id == survey.id).all()
            }
            for ans in answers:
                q = q_map.get(ans.question_id)
                answers_payload.append({
                    "question_id": ans.question_id,
                    "question_text": q.question_text if q else "",
                    "question_type": q.question_type if q else "",
                    "answer_value": ans.answer_value,
                })
            if resp.participant_id:
                history_rows = db.query(Response).filter(
                    Response.participant_id == resp.participant_id
                ).order_by(Response.completed_at.desc()).limit(8).all()
                for h in history_rows:
                    h_survey = db.query(Survey).filter(Survey.id == h.survey_id).first()
                    submission_history.append({
                        "response_id": h.id,
                        "survey_id": h.survey_id,
                        "survey_title": h_survey.title if h_survey else "",
                        "status": h.status,
                        "completed_at": h.completed_at.isoformat() if h.completed_at else None,
                        "client_ip": h.client_ip,
                    })

    display_rows = db.query(ResponseQualityCheck).filter(
        ResponseQualityCheck.survey_id == row.survey_id,
    ).order_by(ResponseQualityCheck.created_at.desc()).all()
    if row.source_type == "excel":
        display_rows = _latest_excel_quality_rows(
            [item for item in display_rows if item.source_type == "excel"]
        )
    else:
        display_rows = [item for item in display_rows if item.source_type == row.source_type]
    row_index = next((i for i, item in enumerate(display_rows) if item.id == row.id), 0)

    return JSONResponse({
        "id": row.id,
        "survey_id": row.survey_id,
        "survey_title": survey.title,
        "source_type": row.source_type,
        "source_ref": row.source_ref,
        "row_label": _quality_row_label(row_index),
        "response_id": row.response_id,
        "participant_email": participant_email,
        "participant_name": participant_name,
        "quality_score": row.quality_score,
        "quality_label": row.quality_label,
        "fraud_risk": row.fraud_risk,
        "rule_penalty": row.rule_penalty,
        "anomaly_score": row.anomaly_score,
        "semantic_risk": row.semantic_risk,
        "triggered_rules": row.triggered_rules or [],
        "reasons": row.reasons or [],
        "llm_result_json": row.llm_result_json,
        "review_status": row.review_status,
        "reviewer_label": row.reviewer_label,
        "notes": row.notes,
        "response_meta": response_meta,
        "answers": answers_payload,
        "raw_response_json": row.raw_response_json,
        "submission_history": submission_history,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })


@app.post("/api/quality-checks/{check_id}/review")
async def review_quality_check(
    check_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row, _survey = _get_quality_check_for_publisher(db, check_id, current_user.id)
    body = await request.json()
    action = (body.get("action") or "").strip().lower()
    notes = body.get("notes")
    reviewer_label = body.get("reviewer_label")

    response = db.query(Response).filter(Response.id == row.response_id).first() if row.response_id else None

    if action == "approve":
        row.review_status = "approved"
        if response:
            release_response_payout(db, response)
    elif action == "reject":
        row.review_status = "rejected"
        if response:
            reject_response_payout(db, response)
    elif action == "pending":
        row.review_status = "pending"
        if response:
            return_response_to_review(db, response)
    elif action == "needs_review":
        row.review_status = "needs_review"
        if response:
            return_response_to_review(db, response)
    elif action == "mark_fraud":
        row.review_status = "rejected"
        row.fraud_risk = True
        row.quality_label = "fraud_risk"
        row.reviewer_label = reviewer_label or "fraud"
        if response:
            reject_response_payout(db, response)
    elif action == "mark_low_quality":
        row.review_status = "rejected"
        row.reviewer_label = reviewer_label or "low_quality"
        if row.quality_label == "high":
            row.quality_label = "low"
        if response:
            reject_response_payout(db, response)
    else:
        raise HTTPException(400, "Invalid action")

    if reviewer_label and action not in {"mark_fraud", "mark_low_quality"}:
        row.reviewer_label = reviewer_label
    if notes is not None:
        row.notes = notes

    db.commit()
    return JSONResponse({
        "message": "review updated",
        "id": row.id,
        "review_status": row.review_status,
        "reviewer_label": row.reviewer_label,
        "fraud_risk": row.fraud_risk,
        "quality_label": row.quality_label,
    })


@app.post("/api/quality-checks/{check_id}/blacklist")
async def blacklist_from_quality_check(
    check_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row, _survey = _get_quality_check_for_publisher(db, check_id, current_user.id)
    body = await request.json()
    block_type = (body.get("block_type") or "").strip().lower()
    reason = body.get("reason") or "Marked from quality review"

    if block_type not in {"ip", "user", "device"}:
        raise HTTPException(400, "block_type must be ip, user, or device")

    block_value = None
    if row.response_id:
        resp = db.query(Response).filter(Response.id == row.response_id).first()
        if resp:
            if block_type == "ip":
                block_value = resp.client_ip
            elif block_type == "user":
                block_value = str(resp.participant_id)
            elif block_type == "device":
                block_value = resp.device_fingerprint

    if not block_value:
        raise HTTPException(400, "No value available to blacklist for this record")

    existing = db.query(QualityBlacklist).filter(
        QualityBlacklist.block_type == block_type,
        QualityBlacklist.block_value == block_value,
    ).first()
    if not existing:
        db.add(QualityBlacklist(
            block_type=block_type,
            block_value=block_value,
            reason=reason,
            created_by=current_user.id,
        ))

    row.fraud_risk = True
    row.review_status = "rejected"
    row.reviewer_label = row.reviewer_label or "blacklisted"
    db.commit()
    return JSONResponse({
        "message": "blacklist updated",
        "block_type": block_type,
        "block_value": block_value,
    })


def _parse_survey_auto_filter_from_form(form) -> tuple:
    """Read quality auto-filter defaults from publish/edit form."""
    enabled_raw = form.get("quality_auto_filter_enabled")
    if isinstance(enabled_raw, list):
        enabled_raw = enabled_raw[-1] if enabled_raw else "0"
    enabled = str(enabled_raw or "").strip().lower() in {"1", "on", "true", "yes"}
    min_score = _parse_auto_approve_min_score(form.get("quality_auto_filter_min_score", 80))
    return enabled, min_score


def _apply_survey_auto_filter_settings(survey: Survey, form) -> None:
    enabled, min_score = _parse_survey_auto_filter_from_form(form)
    survey.quality_auto_filter_enabled = enabled
    survey.quality_auto_filter_min_score = min_score


def _parse_auto_approve_min_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "Auto-approve score must be a number between 0 and 100.")
    return min(100.0, max(0.0, score))


@app.post("/api/surveys/{survey_id}/quality/auto-approve")
async def auto_approve_quality_by_score(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    body = await request.json()
    min_score = _parse_auto_approve_min_score(body.get("min_score", 80))
    scope = (body.get("scope") or "excel").strip().lower()

    rows = db.query(ResponseQualityCheck).filter(
        ResponseQualityCheck.survey_id == survey_id
    ).all()
    if scope == "builtin":
        rows = [row for row in rows if row.source_type == "builtin"]
    elif scope == "excel":
        rows = [row for row in rows if row.source_type == "excel"]

    approved, rejected = apply_auto_approve_checks(rows, min_score)
    response_ids = [row.response_id for row in rows if row.response_id]
    responses_by_id = {}
    if response_ids:
        responses_by_id = {
            response.id: response
            for response in db.query(Response).filter(Response.id.in_(response_ids)).all()
        }
    for row in rows:
        response = responses_by_id.get(row.response_id)
        if not response:
            continue
        if row.review_status == "approved":
            release_response_payout(db, response)
        elif row.review_status == "rejected":
            reject_response_payout(db, response)
    db.commit()
    return JSONResponse({
        "message": "auto filter applied",
        "approved_count": approved,
        "rejected_count": rejected,
        "min_score": min_score,
        "scope": scope,
    })


@app.post("/api/surveys/{survey_id}/quality/import-excel")
def import_excel_for_quality_check(
    survey_id: int,
    file: UploadFile = File(...),
    auto_approve: Optional[str] = Form(None),
    auto_approve_min_score: Optional[float] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    filename = file.filename or "upload.xlsx"
    lower_name = filename.lower()
    if not (lower_name.endswith(".xlsx") or lower_name.endswith(".xlsm")):
        raise HTTPException(400, "Only .xlsx/.xlsm files are supported for now")

    try:
        from openpyxl import load_workbook
    except Exception:
        raise HTTPException(500, "openpyxl is required. Please install dependency first.")

    content = file.file.read()
    try:
        workbook = load_workbook(BytesIO(content), data_only=True)
    except Exception as exc:
        raise HTTPException(400, f"Invalid Excel file: {exc}")

    worksheet = workbook.active
    rows = list(worksheet.iter_rows(values_only=True))
    if len(rows) < 2:
        raise HTTPException(400, "Excel should include a header row and at least one data row")
    if len(rows) - 1 > 500:
        raise HTTPException(400, "Excel has too many rows. Please upload 500 responses or fewer per file.")

    raw_headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    headers = [_normalize_excel_header(h) for h in raw_headers]
    if not any(headers):
        raise HTTPException(400, "Header row is empty")

    replaced_rows = db.query(ResponseQualityCheck).filter(
        ResponseQualityCheck.survey_id == survey_id,
        ResponseQualityCheck.source_type == "excel",
    ).delete(synchronize_session=False)

    questions = db.query(Question).filter(Question.survey_id == survey_id).all()
    question_map = {q.id: q for q in questions}

    header_to_qid = {}
    for idx, header in enumerate(headers):
        if not header:
            continue
        for q in questions:
            candidates = {
                str(q.id).strip().lower(),
                f"q_{q.id}".lower(),
                _normalize_excel_header(q.question_text),
            }
            if header in candidates:
                header_to_qid[idx] = q.id
                break

    historical = []
    completed = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed",
        Response.started_at.isnot(None),
        Response.completed_at.isnot(None),
    ).all()
    for item in completed:
        sec = _duration_seconds_between(item.started_at, item.completed_at)
        if sec:
            historical.append(sec)

    duration_columns = {"duration", "duration_seconds", "time_spent", "completion_seconds"}
    parsed_rows = []

    for row_index, row in enumerate(rows[1:], start=2):
        if row is None:
            continue
        values = list(row)
        if not any(v is not None and str(v).strip() != "" for v in values):
            continue

        answers_by_qid = {}
        row_dict = {}
        for idx, value in enumerate(values):
            header_name = raw_headers[idx] if idx < len(raw_headers) else f"col_{idx+1}"
            safe_value = _json_safe_value(value)
            row_dict[header_name] = safe_value
            if idx in header_to_qid:
                answers_by_qid[header_to_qid[idx]] = safe_value

        duration_seconds = None
        for idx, header in enumerate(headers):
            if header in duration_columns and idx < len(values):
                duration_seconds = _value_as_float(values[idx])
                if duration_seconds is not None:
                    break

        row_question_map, row_answers, row_features = resolve_excel_row_context(
            row_dict=row_dict,
            mapped_question_map=question_map,
            mapped_answers=answers_by_qid,
            duration_seconds=duration_seconds,
        )
        parsed_rows.append({
            "row_index": row_index,
            "row_dict": row_dict,
            "question_map": row_question_map,
            "answers_by_qid": row_answers,
            "duration_seconds": duration_seconds,
            "features": row_features,
        })

    if not parsed_rows:
        raise HTTPException(400, "No data rows found in Excel.")

    all_features = [item["features"] for item in parsed_rows]
    batch_anomalies = batch_anomaly_scores_for_features(all_features)
    imported = 0
    by_label = {"high": 0, "medium": 0, "low": 0, "fraud_risk": 0}
    llm_rows_reviewed = 0
    llm_rows_failed = 0
    created_checks: List[ResponseQualityCheck] = []
    use_auto_approve = str(auto_approve or "").strip().lower() in {"1", "true", "yes", "on"}
    auto_min_score = _parse_auto_approve_min_score(auto_approve_min_score if use_auto_approve else 100)

    for index, item in enumerate(parsed_rows):
        result = compute_excel_row_quality(
            row_dict=item["row_dict"],
            mapped_question_map=item["question_map"],
            mapped_answers=item["answers_by_qid"],
            duration_seconds=item["duration_seconds"],
            historical_durations=historical,
            survey_title=survey.title,
            survey_description=survey.description,
            survey_reward=float(survey.reward_amount or 0.0),
            run_llm=True,
            precomputed_anomaly=batch_anomalies[index] if index < len(batch_anomalies) else None,
        )
        created_checks.append(create_excel_quality_check(
            db,
            survey_id=survey_id,
            source_ref=f"{filename}:row_{item['row_index']}",
            raw_response_json=item["row_dict"],
            result=result,
        ))
        if result.llm_result_json:
            if result.llm_result_json.get("error"):
                llm_rows_failed += 1
            elif not result.llm_result_json.get("skipped"):
                llm_rows_reviewed += 1
        by_label[result.quality_label] = by_label.get(result.quality_label, 0) + 1
        imported += 1

    if imported == 0:
        raise HTTPException(400, "No data rows found in Excel.")

    auto_approved_count = 0
    auto_rejected_count = 0
    if use_auto_approve:
        auto_approved_count, auto_rejected_count = apply_auto_approve_checks(created_checks, auto_min_score)

    db.commit()
    return JSONResponse({
        "message": "excel imported and quality checks generated",
        "survey_id": survey_id,
        "imported_rows": imported,
        "replaced_rows": replaced_rows,
        "label_distribution": by_label,
        "used_generic_columns": len(question_map) == 0,
        "auto_approve": {
            "enabled": use_auto_approve,
            "min_score": auto_min_score if use_auto_approve else None,
            "approved_count": auto_approved_count,
            "rejected_count": auto_rejected_count,
        },
        "llm_summary": {
            "rows_reviewed": llm_rows_reviewed,
            "rows_failed": llm_rows_failed,
            "api_key_configured": anthropic_api_key_configured(),
        },
    })




app.include_router(router)
