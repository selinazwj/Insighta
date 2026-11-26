"""Features / quality / routes."""

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Answer
from app.models import ParticipationDecision
from app.models import Question
from app.models import Response
from app.models import ResponseQualityCheck
from app.models import Survey
from app.models import User
from app.payouts import reject_response_payout
from app.payouts import release_response_payout
from app.payouts import return_response_to_review
from app.quality_engine import ensure_builtin_quality_checks
from app.trust_engine import public_reason_codes


def get_survey_quality_results(
    survey_id: int,
    scope: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.quality.serialization import _latest_excel_quality_rows
    from app.features.quality.serialization import _quality_row_label

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
        trust = None
        if row.response_id:
            resp = db.query(Response).filter(Response.id == row.response_id).first()
            if resp:
                participant = db.query(User).filter(User.id == resp.participant_id).first()
                participant_email = participant.email if participant else None
                trust = db.query(ParticipationDecision).filter(
                    ParticipationDecision.response_id == resp.id,
                ).order_by(ParticipationDecision.created_at.desc()).first()
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
            "trust_outcome": trust.outcome if trust else None,
            "human_status": trust.human_status if trust else None,
            "eligibility_status": trust.eligibility_status if trust else None,
            "assurance_status": trust.assurance_status if trust else None,
            "trust_reason_codes": public_reason_codes(trust.reasons or []) if trust else [],
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return JSONResponse(payload)


def get_quality_check_detail(
    check_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.quality.access import _get_quality_check_for_publisher
    from app.features.quality.serialization import _latest_excel_quality_rows
    from app.features.quality.serialization import _quality_row_label

    row, survey = _get_quality_check_for_publisher(db, check_id, current_user.id)

    participant_email = None
    participant_name = None
    response_meta = None
    trust_payload = None
    answers_payload = []
    submission_history = []

    if row.response_id:
        resp = db.query(Response).filter(Response.id == row.response_id).first()
        if resp:
            participant = db.query(User).filter(User.id == resp.participant_id).first()
            participant_email = participant.email if participant else None
            participant_name = (participant.username or participant.email) if participant else None
            trust = db.query(ParticipationDecision).filter(
                ParticipationDecision.response_id == resp.id,
            ).order_by(ParticipationDecision.created_at.desc()).first()
            if trust:
                trust_payload = {
                    "outcome": trust.outcome,
                    "human_status": trust.human_status,
                    "human_risk_score": trust.human_risk_score,
                    "eligibility_status": trust.eligibility_status,
                    "assurance_status": trust.assurance_status,
                    "quality_status": trust.quality_status,
                    "reason_codes": public_reason_codes(trust.reasons or []),
                    "created_at": trust.created_at.isoformat() if trust.created_at else None,
                }
            response_meta = {
                # Researchers get useful presence/change indicators, not raw
                # security identifiers that could identify a participant.
                "network_signal_available": bool(resp.client_ip),
                "device_signal_available": bool(resp.device_fingerprint),
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
        "trust": trust_payload,
        "answers": answers_payload,
        "raw_response_json": row.raw_response_json,
        "submission_history": submission_history,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })


async def review_quality_check(
    check_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event
    from app.features.quality.access import _get_quality_check_for_publisher
    from app.features.screening.service import _participation_decision

    row, survey = _get_quality_check_for_publisher(db, check_id, current_user.id)
    body = await request.json()
    action = (body.get("action") or "").strip().lower()
    notes = body.get("notes")
    reviewer_label = body.get("reviewer_label")

    response = db.query(Response).filter(Response.id == row.response_id).first() if row.response_id else None
    participant = db.query(User).filter(User.id == response.participant_id).first() if response and response.participant_id else None

    if action == "approve":
        row.review_status = "approved"
        payout_recommendation = None
        if response and participant:
            payout_recommendation, _ = _participation_decision(
                db,
                request,
                participant=participant,
                survey=survey,
                stage="payout",
                response=response,
                record_risk_event=False,
            )
        if response:
            release_response_payout(db, response)
        if survey:
            _record_user_event(
                db,
                request,
                "participant_approved",
                user=current_user,
                target_type="survey",
                target_id=survey.id,
                metadata={
                    "study_title": survey.title,
                    "participant_id": response.participant_id if response else None,
                    "participant_email": participant.email if participant else None,
                    "trust_recommendation": (
                        payout_recommendation.outcome
                        if payout_recommendation else "not_available"
                    ),
                    "source": "Quality review",
                    "user_role": "researcher",
                },
            )
    elif action == "reject":
        row.review_status = "rejected"
        if response:
            reject_response_payout(db, response)
        if survey:
            _record_user_event(
                db,
                request,
                "participant_rejected",
                user=current_user,
                target_type="survey",
                target_id=survey.id,
                metadata={
                    "study_title": survey.title,
                    "participant_id": response.participant_id if response else None,
                    "participant_email": participant.email if participant else None,
                    "source": "Quality review",
                    "user_role": "researcher",
                },
            )
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
        if survey:
            _record_user_event(
                db,
                request,
                "participant_rejected",
                user=current_user,
                target_type="survey",
                target_id=survey.id,
                metadata={
                    "study_title": survey.title,
                    "participant_id": response.participant_id if response else None,
                    "participant_email": participant.email if participant else None,
                    "source": "Quality review",
                    "user_role": "researcher",
                },
            )
    elif action == "mark_low_quality":
        row.review_status = "rejected"
        row.reviewer_label = reviewer_label or "low_quality"
        if row.quality_label == "high":
            row.quality_label = "low"
        if response:
            reject_response_payout(db, response)
        if survey:
            _record_user_event(
                db,
                request,
                "participant_rejected",
                user=current_user,
                target_type="survey",
                target_id=survey.id,
                metadata={
                    "study_title": survey.title,
                    "participant_id": response.participant_id if response else None,
                    "participant_email": participant.email if participant else None,
                    "source": "Quality review",
                    "user_role": "researcher",
                },
            )
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


__all__ = ['get_survey_quality_results', 'get_quality_check_detail', 'review_quality_check']
