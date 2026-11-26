"""Features / verification / routes."""

from datetime import datetime
from datetime import timedelta
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Optional
from urllib.parse import urlsplit
import httpx
from app.bootstrap.runtime import logger
from app.bootstrap.runtime import templates
from app.bootstrap.settings import BASE_URL
from app.bot_defense import check_rate_limit
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.jobs.service import enqueue_didit_session_reconcile_job
from app.models import DiditSession
from app.models import Response
from app.models import Survey
from app.models import User
from app.verification.assurance import TIER_BASIC
from app.verification.assurance import TIER_IDENTITY
from app.verification.assurance import TIER_LABELS
from app.verification.assurance import TIER_RANK
from app.verification.didit_client import create_session as create_didit_session
from app.verification.didit_client import didit_configured
from app.verification.didit_processor import DiditWebhookError
from app.verification.didit_processor import apply_didit_status_update


def participation_review_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    from app.web.middleware import no_store_response

    return no_store_response(templates.TemplateResponse(
        "participation_review.html",
        {"request": request, "current_user": current_user},
    ))


def verification_status(
    session_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Account checks and identity verification are separate signals."""
    from app.features.verification.service import _verification_status_payload
    from app.web.middleware import no_store_response

    return no_store_response(JSONResponse(_verification_status_payload(db, current_user, session_id)))


def start_didit_session(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create/resume a provider session; never accept client account or result fields."""
    from app.features.verification.service import _active_verification_methods
    from app.web.middleware import no_store_response

    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Use application/json")
    origin = request.headers.get("origin")
    allowed_origins = {
        f"{urlsplit(BASE_URL).scheme}://{urlsplit(BASE_URL).netloc}",
        str(request.base_url).rstrip("/"),
    }
    if origin and origin not in allowed_origins:
        raise HTTPException(403, "Cross-origin verification request denied")
    if not didit_configured():
        raise HTTPException(503, "Identity verification is not available yet. Please try again later.")
    # Didit resumes an unfinished session for the same workflow/vendor_data.
    # Do not hold a user lock over the network call: a concurrent webhook also
    # needs to update that user and must be free to finish.
    user = current_user
    if "didit" in _active_verification_methods(db, user.id):
        return no_store_response(JSONResponse({"success": True, "status": "verified", "url": None}))
    try:
        provider = create_didit_session(
            user_id=user.id,
            callback=f"{BASE_URL.rstrip('/')}/verify/didit/return",
        )
        # A webhook may have arrived while the session was being created. It is
        # authoritative; creation must not reset its result back to pending.
        row = db.query(DiditSession).filter(DiditSession.session_id == provider["session_id"]).first()
        if row and row.user_id != user.id:
            raise DiditWebhookError("Didit session belongs to another account", status_code=409)
        if not row:
            row = apply_didit_status_update(
                db, user=user, session_id=provider["session_id"],
                provider_status=provider.get("status") or "Not Started",
                workflow_id=provider["workflow_id"],
                event_at=datetime.utcnow().replace(microsecond=0) if provider.get("status") == "Approved" else None,
            )
        if row.status == "pending":
            enqueue_didit_session_reconcile_job(
                db, session_id=row.session_id, user_id=user.id,
                run_after=datetime.utcnow() + timedelta(minutes=5),
            )
        db.commit()
        return no_store_response(JSONResponse({
            "success": True, "session_id": row.session_id,
            "status": row.status, "url": provider["url"],
        }))
    except DiditWebhookError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message)
    except IntegrityError:
        # Another start or webhook may have inserted the same provider session.
        db.rollback()
        row = db.query(DiditSession).filter(DiditSession.session_id == provider["session_id"]).first()
        if not row or row.user_id != current_user.id:
            raise HTTPException(409, "Verification session could not be linked. Please try again.")
        return no_store_response(JSONResponse({
            "success": True, "session_id": row.session_id,
            "status": row.status, "url": provider["url"],
        }))
    except (httpx.HTTPError, ValueError, RuntimeError):
        db.rollback()
        logger.warning("Didit session creation failed for user_id=%s", current_user.id)
        raise HTTPException(502, "We could not start identity verification. Please try again.")


def didit_return_page(
    request: Request,
    verificationSessionId: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Browser query parameters (including status=Approved) are never proof.
    from app.features.verification.service import _verification_status_payload
    from app.web.middleware import no_store_response

    payload = _verification_status_payload(db, current_user, verificationSessionId)
    row = payload["didit"]
    if row and row["status"] == "pending":
        enqueue_didit_session_reconcile_job(db, session_id=row["session_id"], user_id=current_user.id)
        db.commit()
    response = templates.TemplateResponse("didit_result.html", {
        "request": request, "current_user": current_user,
        "didit_session_id": row["session_id"] if row else "",
    })
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return no_store_response(response)


def participant_verification_status(
    participant_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Researcher-facing verification status for a participant in their studies."""
    from app.features.verification.service import _verification_status_payload
    from app.web.middleware import no_store_response

    is_publisher = db.query(Survey.id).filter(
        Survey.publisher_id == current_user.id
    ).first() is not None
    if not is_publisher and current_user.id != participant_id:
        raise HTTPException(403, "Researchers only")

    if current_user.id != participant_id:
        linked = db.query(Response.id).join(Survey, Survey.id == Response.survey_id).filter(
            Response.participant_id == participant_id,
            Survey.publisher_id == current_user.id,
        ).first()
        if not linked:
            raise HTTPException(404, "Participant not found in your studies")

    participant = db.query(User).filter(User.id == participant_id).first()
    if not participant:
        raise HTTPException(404, "Participant not found")
    return no_store_response(JSONResponse(_verification_status_payload(db, participant)))


def verify_page(
    request: Request,
    verify_error: Optional[str] = None,
    needs_tier: Optional[str] = None,
    needs_domains: Optional[str] = None,
    done: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Account verification uses email control and optional LinkedIn evidence.
    # needs_tier / needs_domains come from the survey completion redirect and tell
    # the participant WHY theyre being sent here. done=1 means they JUST finished
    # a verification step, so we show a clean success screen, not more tiers
    from app.features.verification.service import _normalize_domains
    from app.features.verification.service import _render_verify

    domains = _normalize_domains(needs_domains)
    notice = None
    if domains:
        notice = f"This study is limited to emails at: {', '.join(domains)}. Verify one of these addresses below to receive your payout."
    elif needs_tier == TIER_IDENTITY:
        notice = "This study requires an ID check with Didit. Email or professional account checks alone do not meet this requirement."
    elif needs_tier in TIER_RANK:
        notice = f"This study requires {TIER_LABELS[needs_tier]}. Complete the available check below."
    return _render_verify(request, current_user, db, error=verify_error, notice=notice,
                          needs_domains=needs_domains, focus_tier=needs_tier, done=bool(done))


def verify_send_code(
    email: Optional[str] = Form(None),
    needs_domains: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # ive added this for verification — sends an OTP to whatever email theyre proving
    # (defaults to their account email, but can be e.g. their @bu.edu address)
    from app.features.auth.codes import _issue_verification_code
    from app.features.auth.codes import _mask_email
    from app.features.auth.policy import _normalize_email
    from app.features.messaging.verification_messages import _send_verification_email
    from app.features.verification.service import _domains_pretty
    from app.features.verification.service import _email_domain
    from app.features.verification.service import _normalize_domains

    target_email = _normalize_email(email or "") or current_user.email
    if not target_email or "@" not in target_email:
        return JSONResponse({"ok": False, "message": "Please enter a valid email address first."}, status_code=400)
    # ive added this so we dont even send a code to a wrong-domain email — the
    # study restricts which domains count, so tell them right away
    domains = _normalize_domains(needs_domains)
    if domains and _email_domain(target_email) not in domains:
        return JSONResponse({"ok": False, "message": f"This study only accepts emails ending in {_domains_pretty(domains)}. Please enter one of those."}, status_code=400)
    # phase 4 — dont let one user spam the mailer
    if not check_rate_limit(f"verify-send:user:{current_user.id}", max_hits=5, window_seconds=600):
        return JSONResponse({"ok": False, "message": "Too many codes requested. Please wait a few minutes."}, status_code=429)
    code = _issue_verification_code(db, target_email, "verify_identity")
    _send_verification_email(target_email, "verify_identity", code)
    return JSONResponse({
        "ok": True,
        "message": f"Verification code sent to {_mask_email(target_email)}."
    })


def verify_user(
    request: Request,
    verification_code: str = Form(...),
    email: Optional[str] = Form(None),
    needs_domains: Optional[str] = Form(None),
    needs_tier: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # ive added this for verification — checks the OTP for the given email, then
    # records verified_email so domain-restricted studies can check it
    from app.features.auth.codes import _consume_verification_code
    from app.features.auth.policy import _normalize_email
    from app.features.verification.service import _current_assurance_tier
    from app.features.verification.service import _domains_pretty
    from app.features.verification.service import _email_domain
    from app.features.verification.service import _normalize_domains
    from app.features.verification.service import _record_verified_evidence
    from app.features.verification.service import _render_verify
    from app.features.verification.service import _tier_rank
    from app.features.verification.service import _verified_evidence_owner

    target_email = _normalize_email(email or "") or current_user.email
    # ive added this so a wrong-domain email is rejected here, not silently later
    domains = _normalize_domains(needs_domains)
    if domains and _email_domain(target_email) not in domains:
        return _render_verify(request, current_user, db, needs_domains=needs_domains, prefill_email=target_email, focus_tier=needs_tier,
            error=f"This study only accepts emails ending in {_domains_pretty(domains)}. Please verify one of those addresses.")
    evidence_owner = _verified_evidence_owner(
        db,
        method="email_otp",
        subject=target_email,
        excluding_user_id=current_user.id,
    )
    if evidence_owner:
        return _render_verify(
            request,
            current_user,
            db,
            needs_domains=needs_domains,
            prefill_email=target_email,
            focus_tier=needs_tier,
            error="That verified email is already linked to another participant account.",
        )
    # phase 4 — a 6 digit code is brute-forceable, so cap the attempts hard
    if not check_rate_limit(f"verify-otp:user:{current_user.id}", max_hits=5, window_seconds=600):
        return _render_verify(request, current_user, db, needs_domains=needs_domains, prefill_email=target_email, focus_tier=needs_tier,
            error="Too many attempts. Please wait a few minutes and request a new code.")
    if not _consume_verification_code(db, target_email, "verify_identity", verification_code):
        return _render_verify(request, current_user, db, needs_domains=needs_domains, prefill_email=target_email, focus_tier=needs_tier,
            error="Invalid or expired verification code. Please request a new one.")
    # they proved ownership of target_email
    current_user.verified_email = target_email
    current_user.verification_status = "verified"
    _record_verified_evidence(
        db,
        user_id=current_user.id,
        attribute="email_control",
        method="email_otp",
        subject=target_email,
        trust_score=0.35,
    )
    # Store the derived cache for old UI/code paths. It is not the source of truth.
    derived_tier = _current_assurance_tier(db, current_user) or TIER_BASIC
    if _tier_rank(derived_tier) < _tier_rank(getattr(current_user, "verified_tier", None)):
        current_user.verified_tier = derived_tier
    elif not getattr(current_user, "verified_tier", None):
        current_user.verified_tier = TIER_BASIC
    current_user.verified_at = datetime.utcnow()
    db.commit()
    # done=1 shows the clean success screen instead of more tier options
    return RedirectResponse("/verify?done=1", status_code=303)


__all__ = ['participation_review_page', 'verification_status', 'start_didit_session', 'didit_return_page', 'participant_verification_status', 'verify_page', 'verify_send_code', 'verify_user']
