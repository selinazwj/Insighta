"""Features / quality / reviews."""

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Any
import os
from app.ai_usage import AiUsageLimitError
from app.ai_usage import ensure_usage_available
from app.ai_usage import find_recent_ai_job
from app.ai_usage import job_public_payload
from app.ai_usage import reserve_usage
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.jobs.service import AI_QUALITY_REVIEW
from app.jobs.service import DONE
from app.jobs.service import enqueue_ai_quality_review_job
from app.models import QualityBlacklist
from app.models import Response
from app.models import ResponseQualityCheck
from app.models import Survey
from app.models import User
from app.payouts import reject_response_payout
from app.payouts import release_response_payout
from app.quality_engine import apply_auto_approve_checks


def _queue_builtin_ai_quality_review(
    db: Session,
    *,
    current_user: User,
    survey: Survey,
    rows: list[ResponseQualityCheck],
) -> JSONResponse:
    check_ids = sorted({row.id for row in rows if row.source_type == "builtin" and row.response_id})
    if not check_ids:
        raise HTTPException(400, "No built-in responses are available for AI review")
    batch_limit = max(1, min(10, int(os.environ.get("AI_QUALITY_BATCH_LIMIT", "5"))))
    if len(check_ids) > batch_limit:
        raise HTTPException(400, f"Select at most {batch_limit} responses per AI review")
    fingerprint = f"quality:{current_user.id}:{survey.id}:{','.join(str(value) for value in check_ids)}"
    recent = find_recent_ai_job(
        db,
        job_type=AI_QUALITY_REVIEW,
        user_id=current_user.id,
        fingerprint=fingerprint,
    )
    if recent:
        payload = job_public_payload(recent)
        payload["reused"] = True
        return JSONResponse(payload, status_code=200 if recent.status == DONE else 202)
    try:
        ensure_usage_available(db, user_id=current_user.id, units=len(check_ids))
    except AiUsageLimitError as exc:
        raise HTTPException(429, str(exc)) from exc
    job = enqueue_ai_quality_review_job(
        db,
        user_id=current_user.id,
        survey_id=survey.id,
        check_ids=check_ids,
        fingerprint=fingerprint,
    )
    reserve_usage(
        db,
        user_id=current_user.id,
        job=job,
        feature="builtin_quality_review",
        units=len(check_ids),
        survey_id=survey.id,
        metadata={"check_count": len(check_ids)},
    )
    db.commit()
    payload = job_public_payload(job)
    payload["reused"] = False
    return JSONResponse(payload, status_code=202)


def queue_single_quality_ai_review(
    check_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.quality.access import _get_quality_check_for_publisher

    row, survey = _get_quality_check_for_publisher(db, check_id, current_user.id)
    if row.source_type != "builtin":
        raise HTTPException(400, "This action is for built-in responses")
    return _queue_builtin_ai_quality_review(
        db,
        current_user=current_user,
        survey=survey,
        rows=[row],
    )


async def queue_flagged_quality_ai_review(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey or survey.form_url != "__builtin__":
        raise HTTPException(404, "Built-in survey not found")
    body = await request.json()
    supplied_ids = []
    for value in body.get("check_ids") or []:
        try:
            supplied_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    query = db.query(ResponseQualityCheck).filter(
        ResponseQualityCheck.survey_id == survey.id,
        ResponseQualityCheck.source_type == "builtin",
    )
    if supplied_ids:
        rows = query.filter(ResponseQualityCheck.id.in_(supplied_ids)).all()
    else:
        rows = query.filter(or_(
            ResponseQualityCheck.fraud_risk.is_(True),
            ResponseQualityCheck.quality_label.in_(["medium", "low", "fraud_risk"]),
            ResponseQualityCheck.review_status.in_(["pending", "needs_review"]),
        )).order_by(ResponseQualityCheck.created_at.desc()).limit(
            max(1, min(10, int(os.environ.get("AI_QUALITY_BATCH_LIMIT", "5"))))
        ).all()
    return _queue_builtin_ai_quality_review(
        db,
        current_user=current_user,
        survey=survey,
        rows=rows,
    )


async def blacklist_from_quality_check(
    check_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.auth.policy import _normalize_email
    from app.features.quality.access import _get_quality_check_for_publisher

    row, _survey = _get_quality_check_for_publisher(db, check_id, current_user.id)
    body = await request.json()
    block_type = (body.get("block_type") or "").strip().lower()
    reason = body.get("reason") or "Marked from quality review"

    if block_type not in {"ip", "user", "device", "email"}:
        raise HTTPException(400, "block_type must be ip, user, device, or email")

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
            elif block_type == "email" and resp.participant_id:
                participant = db.query(User).filter(User.id == resp.participant_id).first()
                block_value = _normalize_email(participant.email if participant else "")

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


def _parse_auto_approve_min_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "Auto-approve score must be a number between 0 and 100.")
    return min(100.0, max(0.0, score))


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


__all__ = ['_queue_builtin_ai_quality_review', 'queue_single_quality_ai_review', 'queue_flagged_quality_ai_review', 'blacklist_from_quality_check', '_parse_auto_approve_min_score', 'auto_approve_quality_by_score']
