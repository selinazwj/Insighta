"""Features / admin / trust."""

from datetime import datetime
from datetime import timedelta
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.features.auth.policy import DEFAULT_BLOCKED_EMAILS
from app.models import ParticipationDecision
from app.models import QualityBlacklist
from app.models import Survey
from app.models import User
from app.models import Verification
from app.trust_engine import public_reason_codes
from app.verification.assurance import evidence_reference


ADMIN_TRUST_REVIEW_METHOD = "admin_review"


ADMIN_TRUST_REVIEW_ATTRIBUTE = "account_trust_review"


ADMIN_TRUST_APPROVAL_DAYS = 30


ADMIN_TRUST_BLOCK_PREFIX = "[trust-review]"


def _latest_flagged_account_decisions(db: Session, *, limit: int = 500) -> list[ParticipationDecision]:
    """Return the newest unresolved-worthy account signal per participant.

    Eligibility and assurance failures are study prerequisites, not evidence
    that an account is abusive, so they do not enter this admin account queue.
    """
    flagged_ids = db.query(
        func.max(ParticipationDecision.id).label("decision_id"),
    ).filter(
        or_(
            ParticipationDecision.human_status.in_(["review", "blocked"]),
            ParticipationDecision.quality_status.in_([
                "low", "fraud_risk", "needs_review",
            ]),
        ),
    ).group_by(ParticipationDecision.participant_id).subquery()
    return db.query(ParticipationDecision).join(
        flagged_ids,
        ParticipationDecision.id == flagged_ids.c.decision_id,
    ).order_by(
        ParticipationDecision.created_at.desc(),
        ParticipationDecision.id.desc(),
    ).limit(limit).all()


def _admin_trust_review_for_user(db: Session, user_id: int) -> Optional[Verification]:
    return db.query(Verification).filter(
        Verification.user_id == user_id,
        Verification.method == ADMIN_TRUST_REVIEW_METHOD,
        Verification.attribute == ADMIN_TRUST_REVIEW_ATTRIBUTE,
    ).order_by(Verification.verified_at.desc(), Verification.id.desc()).first()


def _trust_review_resolution(
    db: Session,
    *,
    user: User,
    decision: ParticipationDecision,
    review: Optional[Verification],
    now: datetime,
) -> str:
    from app.features.auth.policy import _is_email_blocked

    if _is_email_blocked(db, user.email):
        return "blocked"
    if not review or not review.verified_at or review.verified_at < decision.created_at:
        return "pending"
    if review.status == "rejected":
        return "blocked"
    if review.status == "verified" and review.expires_at and review.expires_at > now:
        return "approved"
    return "pending"


async def admin_trust_reviews(
    admin_key: str = Query(None),
    status: str = Query("pending"),
    db: Session = Depends(get_db),
):
    from app.features.admin.access import _admin_key_matches

    if not _admin_key_matches(admin_key):
        raise HTTPException(403, "Unauthorized")
    normalized_status = (status or "pending").strip().lower()
    if normalized_status not in {"all", "pending", "approved", "blocked"}:
        raise HTTPException(400, "status must be all, pending, approved, or blocked")

    decisions = _latest_flagged_account_decisions(db)
    participant_ids = [row.participant_id for row in decisions]
    users = {
        user.id: user
        for user in db.query(User).filter(User.id.in_(participant_ids)).all()
    } if participant_ids else {}
    survey_ids = {row.survey_id for row in decisions if row.survey_id}
    surveys = {
        survey.id: survey
        for survey in db.query(Survey).filter(Survey.id.in_(survey_ids)).all()
    } if survey_ids else {}
    # The admin-review evidence is unique per user in normal operation. Build
    # this explicitly so legacy duplicates still select the newest row.
    reviews: dict[int, Verification] = {}
    if participant_ids:
        for row in db.query(Verification).filter(
            Verification.user_id.in_(participant_ids),
            Verification.method == ADMIN_TRUST_REVIEW_METHOD,
            Verification.attribute == ADMIN_TRUST_REVIEW_ATTRIBUTE,
        ).order_by(Verification.verified_at.desc(), Verification.id.desc()).all():
            reviews.setdefault(row.user_id, row)

    now = datetime.utcnow()
    counts = {"pending": 0, "approved": 0, "blocked": 0}
    items = []
    for decision in decisions:
        user = users.get(decision.participant_id)
        if not user:
            continue
        review = reviews.get(user.id)
        resolution = _trust_review_resolution(
            db,
            user=user,
            decision=decision,
            review=review,
            now=now,
        )
        counts[resolution] += 1
        if normalized_status != "all" and resolution != normalized_status:
            continue
        survey = surveys.get(decision.survey_id)
        reasons = [
            str(reason)[:240]
            for reason in (decision.reasons or [])
            if str(reason).strip()
        ]
        items.append({
            "user_id": user.id,
            "email": user.email,
            "name": " ".join(filter(None, [user.first_name, user.last_name])) or user.username or "Participant",
            "decision_id": decision.id,
            "stage": decision.stage,
            "outcome": decision.outcome,
            "human_status": decision.human_status,
            "human_risk_score": decision.human_risk_score,
            "quality_status": decision.quality_status,
            "reason_codes": public_reason_codes(decision.reasons or []),
            "reasons": list(dict.fromkeys(reasons)),
            "survey_id": decision.survey_id,
            "survey_title": survey.title if survey else "",
            "flagged_at": decision.created_at.isoformat() if decision.created_at else None,
            "review_status": resolution,
            "reviewed_at": review.verified_at.isoformat() if review and review.verified_at else None,
            "approval_expires_at": review.expires_at.isoformat() if review and review.expires_at else None,
        })
    return JSONResponse({
        "status": normalized_status,
        "counts": counts,
        "flagged_accounts": items,
    })


async def admin_resolve_trust_review(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    from app.features.admin.access import _admin_key_matches
    from app.features.auth.policy import _configured_blocked_emails
    from app.features.auth.policy import _is_email_blocked
    from app.features.auth.policy import _normalize_email

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")):
        raise HTTPException(403, "Unauthorized")
    action = (body.get("action") or "").strip().lower()
    note = (body.get("note") or "").strip()[:400]
    if action not in {"approve", "block"}:
        raise HTTPException(400, "action must be approve or block")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    latest_flag = next((
        row for row in _latest_flagged_account_decisions(db)
        if row.participant_id == user_id
    ), None)
    if not latest_flag:
        raise HTTPException(404, "No flagged trust decision was found for this account")

    now = datetime.utcnow()
    review = _admin_trust_review_for_user(db, user_id)
    if not review:
        review = Verification(
            user_id=user_id,
            attribute=ADMIN_TRUST_REVIEW_ATTRIBUTE,
            method=ADMIN_TRUST_REVIEW_METHOD,
            evidence_ref=evidence_reference(ADMIN_TRUST_REVIEW_METHOD, str(user_id)),
        )
        db.add(review)
    review.verified_at = now
    review.verified_by = "admin"
    review.trust_score = latest_flag.human_risk_score

    normalized_email = _normalize_email(user.email)
    if action == "approve":
        if normalized_email in DEFAULT_BLOCKED_EMAILS or normalized_email in _configured_blocked_emails():
            raise HTTPException(409, "This account is blocked by a protected system list and cannot be approved here")
        db.query(QualityBlacklist).filter(
            QualityBlacklist.block_type == "email",
            QualityBlacklist.block_value == normalized_email,
            QualityBlacklist.reason.like(f"{ADMIN_TRUST_BLOCK_PREFIX}%"),
        ).delete(synchronize_session=False)
        if _is_email_blocked(db, normalized_email):
            raise HTTPException(409, "Remove the account's separate admin block before approving it")
        review.status = "verified"
        review.expires_at = now + timedelta(days=ADMIN_TRUST_APPROVAL_DAYS)
        message = f"Account approved for {ADMIN_TRUST_APPROVAL_DAYS} days"
    else:
        review.status = "rejected"
        review.expires_at = None
        block_reason = f"{ADMIN_TRUST_BLOCK_PREFIX} {note or 'Blocked from flagged-account review'}"
        existing_block = db.query(QualityBlacklist).filter(
            QualityBlacklist.block_type == "email",
            QualityBlacklist.block_value == normalized_email,
        ).first()
        if existing_block:
            # Do not relabel a separate admin/quality block as a trust-review
            # block; otherwise a later approval could accidentally remove it.
            if (existing_block.reason or "").startswith(ADMIN_TRUST_BLOCK_PREFIX):
                existing_block.reason = block_reason
        else:
            db.add(QualityBlacklist(
                block_type="email",
                block_value=normalized_email,
                reason=block_reason,
            ))
        message = "Account blocked"

    db.commit()
    return JSONResponse({
        "ok": True,
        "user_id": user_id,
        "action": action,
        "message": message,
        "approval_expires_at": review.expires_at.isoformat() if review.expires_at else None,
    })


async def admin_blocked_users(admin_key: str = Query(None), db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.auth.policy import _normalize_email

    if not _admin_key_matches(admin_key):
        raise HTTPException(403, "Unauthorized")
    rows = db.query(QualityBlacklist).filter(
        QualityBlacklist.block_type == "email",
    ).order_by(QualityBlacklist.created_at.desc()).all()
    seen = set()
    result = []
    for email in sorted(DEFAULT_BLOCKED_EMAILS):
        normalized = _normalize_email(email)
        if not normalized:
            continue
        seen.add(normalized)
        result.append({
            "email": normalized,
            "reason": "Blocked by default risk-control list",
            "source": "built_in",
            "created_at": "",
        })
    for row in rows:
        normalized = _normalize_email(row.block_value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append({
            "email": normalized,
            "reason": row.reason or "",
            "source": "database",
            "created_at": row.created_at.isoformat() if row.created_at else "",
        })
    return JSONResponse({"blocked_users": result, "count": len(result)})


async def admin_block_user_by_email(request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.auth.policy import _normalize_email

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")):
        raise HTTPException(403, "Unauthorized")
    email = _normalize_email(body.get("email"))
    reason = (body.get("reason") or "Blocked by admin").strip()[:500]
    if not email or "@" not in email:
        raise HTTPException(400, "Please provide a valid email address")
    if email in DEFAULT_BLOCKED_EMAILS:
        return JSONResponse({
            "message": "Email is already blocked",
            "email": email,
            "source": "built_in",
            "created": False,
        })
    existing = db.query(QualityBlacklist).filter(
        QualityBlacklist.block_type == "email",
        QualityBlacklist.block_value == email,
    ).first()
    if existing:
        if reason and existing.reason != reason:
            existing.reason = reason
            db.commit()
        return JSONResponse({
            "message": "Email is already blocked",
            "email": email,
            "source": "database",
            "created": False,
        })
    db.add(QualityBlacklist(
        block_type="email",
        block_value=email,
        reason=reason,
    ))
    db.commit()
    return JSONResponse({
        "message": "Email blocked",
        "email": email,
        "source": "database",
        "created": True,
    })


__all__ = ['ADMIN_TRUST_REVIEW_METHOD', 'ADMIN_TRUST_REVIEW_ATTRIBUTE', 'ADMIN_TRUST_APPROVAL_DAYS', 'ADMIN_TRUST_BLOCK_PREFIX', '_latest_flagged_account_decisions', '_admin_trust_review_for_user', '_trust_review_resolution', 'admin_trust_reviews', 'admin_resolve_trust_review', 'admin_blocked_users', 'admin_block_user_by_email']
