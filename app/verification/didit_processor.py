"""Didit webhook processing, DB updates, and session reconciliation."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import DiditSession, DiditWebhookEvent, User, Verification
from app.verification.assurance import evidence_reference, strongest_tier_for_methods
from app.verification.didit import (
    DIDIT_ATTRIBUTE,
    DIDIT_METHOD,
    map_provider_status,
    parse_vendor_user_id,
)
from app.verification.didit_client import fetch_session_decision

TERMINAL_DIDIT_STATUSES = frozenset({"verified", "rejected"})


class DiditWebhookDuplicate(Exception):
    """event_id was already processed."""


class DiditWebhookIgnored(Exception):
    def __init__(self, reason: str = "") -> None:
        self.reason = reason


class DiditWebhookError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code


def upsert_didit_session(
    db: Session,
    *,
    user_id: int,
    session_id: str,
    provider_status: Optional[str] = None,
    workflow_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> DiditSession:
    row = db.query(DiditSession).filter(DiditSession.session_id == session_id).first()
    now = datetime.utcnow()
    if not row:
        row = DiditSession(
            user_id=user_id,
            session_id=session_id,
            status="pending",
            created_at=now,
        )
        db.add(row)
    elif row.user_id != user_id:
        raise DiditWebhookError(
            "Didit session is already linked to another account",
            status_code=409,
        )
    if row.workflow_id and workflow_id and row.workflow_id != workflow_id:
        raise DiditWebhookError("Didit session belongs to another workflow", status_code=409)
    if workflow_id:
        row.workflow_id = workflow_id
    if provider_status is not None:
        row.provider_status = provider_status
        row.status = map_provider_status(provider_status)
    if event_id:
        row.last_event_id = event_id
    row.updated_at = now
    return row


def _active_verification_methods(db: Session, user_id: int) -> set[str]:
    now = datetime.utcnow()
    rows = db.query(Verification).filter(
        Verification.user_id == user_id,
        Verification.status == "verified",
        or_(Verification.expires_at.is_(None), Verification.expires_at > now),
    ).all()
    return {(row.method or "").strip().lower() for row in rows if row.method}


def _refresh_user_verified_tier(db: Session, user: User) -> None:
    user.verified_tier = strongest_tier_for_methods(_active_verification_methods(db, user.id))
    user.verification_status = "verified" if user.verified_tier else "unverified"
    if not user.verified_tier:
        user.verified_at = None


def _record_didit_evidence(
    db: Session,
    *,
    user_id: int,
    session_id: str,
    trust_score: float = 1.0,
    valid_days: int = 365,
) -> Verification:
    ref = evidence_reference(DIDIT_METHOD, session_id)
    existing = db.query(Verification).filter(
        Verification.user_id == user_id,
        Verification.method == DIDIT_METHOD,
        Verification.evidence_ref == ref,
    ).first()
    now = datetime.utcnow()
    row = existing or Verification(user_id=user_id, method=DIDIT_METHOD, evidence_ref=ref)
    row.attribute = DIDIT_ATTRIBUTE
    row.status = "verified"
    row.trust_score = trust_score
    row.verified_at = now
    row.expires_at = now + timedelta(days=valid_days)
    row.verified_by = "system"
    if not existing:
        db.add(row)
    return row


def sync_didit_evidence(
    db: Session,
    *,
    user: User,
    session_id: str,
    local_status: str,
) -> None:
    if local_status == "verified":
        _record_didit_evidence(db, user_id=user.id, session_id=session_id)
        user.verification_status = "verified"
        user.verified_at = datetime.utcnow()
        return

    if local_status != "verified":
        ref = evidence_reference(DIDIT_METHOD, session_id)
        row = db.query(Verification).filter(
            Verification.user_id == user.id,
            Verification.method == DIDIT_METHOD,
            Verification.evidence_ref == ref,
        ).first()
        if row and row.status != local_status:
            row.status = local_status
            row.verified_by = "system"


def apply_didit_status_update(
    db: Session,
    *,
    user: User,
    session_id: str,
    provider_status: str,
    workflow_id: Optional[str] = None,
    event_id: Optional[str] = None,
    event_at: Optional[datetime] = None,
) -> DiditSession:
    existing = db.query(DiditSession).filter(DiditSession.session_id == session_id).populate_existing().with_for_update().first()
    if existing and existing.user_id != user.id:
        raise DiditWebhookError("Didit session belongs to another account", status_code=409)
    if existing and event_at and existing.provider_event_at and event_at < existing.provider_event_at:
        return existing
    session_row = upsert_didit_session(
        db,
        user_id=user.id,
        session_id=session_id,
        provider_status=provider_status,
        workflow_id=workflow_id,
        event_id=event_id,
    )
    if event_at:
        session_row.provider_event_at = event_at
    sync_didit_evidence(
        db,
        user=user,
        session_id=session_id,
        local_status=session_row.status,
    )
    db.flush()
    _refresh_user_verified_tier(db, user)
    return session_row


def _resolve_user_id(db: Session, body: dict[str, Any], session_id: str) -> int:
    user_id = parse_vendor_user_id(
        body.get("vendor_data"),
        body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
    )
    session_row = db.query(DiditSession).filter(DiditSession.session_id == session_id).first()
    if user_id is None and session_row is not None:
        user_id = session_row.user_id
    if user_id is None:
        raise DiditWebhookError(
            "Unable to resolve user from vendor_data or registered session",
            status_code=400,
        )
    return int(user_id)


def _record_processed_event(
    db: Session,
    *,
    event_id: str,
    session_id: Optional[str],
    webhook_type: Optional[str],
    provider_status: Optional[str],
) -> None:
    db.add(DiditWebhookEvent(
        event_id=event_id,
        session_id=session_id,
        webhook_type=webhook_type,
        provider_status=provider_status,
    ))


def process_didit_webhook_body(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    """Apply a verified Didit webhook payload to local session + evidence tables."""
    event_id = str(body.get("event_id") or "").strip()
    webhook_type = str(body.get("webhook_type") or "").strip()
    session_id = str(body.get("session_id") or "").strip()
    provider_status = str(body.get("status") or "").strip()

    if event_id:
        existing_event = db.query(DiditWebhookEvent).filter(
            DiditWebhookEvent.event_id == event_id
        ).first()
        if existing_event:
            raise DiditWebhookDuplicate()

    if webhook_type and webhook_type not in {"status.updated", "data.updated"}:
        if event_id:
            _record_processed_event(
                db,
                event_id=event_id,
                session_id=session_id or None,
                webhook_type=webhook_type or None,
                provider_status=provider_status or None,
            )
        raise DiditWebhookIgnored(webhook_type)

    if not session_id:
        raise DiditWebhookError("session_id is required", status_code=400)

    user_id = _resolve_user_id(db, body, session_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise DiditWebhookError("User not found", status_code=404)

    workflow_id = str(body.get("workflow_id") or "").strip() or None
    expected_workflow = (os.environ.get("DIDIT_WORKFLOW_ID") or "").strip()
    if expected_workflow and workflow_id and workflow_id != expected_workflow:
        raise DiditWebhookError("Unexpected Didit workflow", status_code=400)
    try:
        event_time = body.get("created_at") or body.get("timestamp")
        event_at = datetime.utcfromtimestamp(int(event_time)) if event_time is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        raise DiditWebhookError("Invalid Didit event timestamp")

    apply_didit_status_update(
        db,
        user=user,
        session_id=session_id,
        provider_status=provider_status or "In Progress",
        workflow_id=workflow_id,
        event_id=event_id or None,
        event_at=event_at,
    )
    if event_id:
        _record_processed_event(
            db,
            event_id=event_id,
            session_id=session_id,
            webhook_type=webhook_type or None,
            provider_status=provider_status or None,
        )
    return {
        "session_id": session_id,
        "user_id": user_id,
        "provider_status": provider_status or "In Progress",
    }


def reconcile_didit_session(
    db: Session,
    *,
    session_id: str,
    user_id: int,
) -> dict[str, Any]:
    """Poll Didit decision API and sync local state for a pending session."""
    session_row = db.query(DiditSession).filter(DiditSession.session_id == session_id).first()
    if not session_row or session_row.user_id != user_id:
        raise RuntimeError("Didit session is not registered to this account")
    if session_row and session_row.status in TERMINAL_DIDIT_STATUSES:
        return {
            "session_id": session_id,
            "status": session_row.status,
            "skipped": True,
            "reason": "already_terminal",
        }

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise RuntimeError(f"User {user_id} not found for Didit reconcile")

    fetched_at = datetime.utcnow()
    decision = fetch_session_decision(session_id)
    if parse_vendor_user_id(decision.get("vendor_data")) != user_id:
        raise RuntimeError("Didit decision belongs to another account")
    expected_workflow = session_row.workflow_id or (os.environ.get("DIDIT_WORKFLOW_ID") or "").strip()
    if expected_workflow and decision.get("workflow_id") != expected_workflow:
        raise RuntimeError("Didit decision belongs to another workflow")
    provider_status = str(decision.get("status") or "").strip() or "In Progress"
    workflow_id = str(decision.get("workflow_id") or "").strip() or None
    row = apply_didit_status_update(
        db,
        user=user,
        session_id=session_id,
        provider_status=provider_status,
        workflow_id=workflow_id,
        event_at=fetched_at,
    )
    return {
        "session_id": session_id,
        "status": row.status,
        "provider_status": row.provider_status,
        "reconciled": True,
    }


def enqueue_stale_didit_reconcile_jobs(
    db: Session,
    *,
    pending_minutes: int = 10,
    limit: int = 25,
) -> int:
    """Queue reconcile jobs for sessions stuck in pending without a terminal webhook."""
    from app.jobs.service import enqueue_didit_session_reconcile_job

    cutoff = datetime.utcnow() - timedelta(minutes=max(1, pending_minutes))
    rows = (
        db.query(DiditSession)
        .filter(
            DiditSession.status == "pending",
            DiditSession.updated_at <= cutoff,
        )
        .order_by(DiditSession.updated_at.asc(), DiditSession.id.asc())
        .limit(max(1, limit))
        .all()
    )
    for row in rows:
        enqueue_didit_session_reconcile_job(
            db,
            session_id=row.session_id,
            user_id=row.user_id,
        )
    return len(rows)
