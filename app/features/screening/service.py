"""Features / screening / service."""

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional
from urllib.parse import urlencode
from app.models import ParticipationDecision
from app.models import Response
from app.models import ResponseQualityCheck
from app.models import ScreeningAttempt
from app.models import ScreeningQuestion
from app.models import Survey
from app.models import User
from app.trust_engine import ParticipationInputs
from app.trust_engine import public_reason_codes
from app.trust_engine import recommend_participation
from app.verification.assurance import TIER_BASIC


def _approved_screening_questions(db: Session, survey_id: int) -> list[ScreeningQuestion]:
    latest_version = db.query(func.max(ScreeningQuestion.version)).filter(
        ScreeningQuestion.survey_id == survey_id,
        ScreeningQuestion.status == "approved",
    ).scalar()
    if not latest_version:
        return []
    return db.query(ScreeningQuestion).filter(
        ScreeningQuestion.survey_id == survey_id,
        ScreeningQuestion.version == latest_version,
        ScreeningQuestion.status == "approved",
    ).order_by(ScreeningQuestion.order_index.asc()).all()


def _screening_passed(db: Session, survey_id: int, participant_id: int) -> bool:
    questions = _approved_screening_questions(db, survey_id)
    if not questions:
        return True
    version = questions[0].version
    return db.query(ScreeningAttempt).filter(
        ScreeningAttempt.survey_id == survey_id,
        ScreeningAttempt.participant_id == participant_id,
        ScreeningAttempt.version == version,
        ScreeningAttempt.status == "passed",
    ).first() is not None


def _screening_status(db: Session, survey_id: int, participant_id: int) -> str:
    questions = _approved_screening_questions(db, survey_id)
    if not questions:
        return "not_required"
    version = questions[0].version
    attempt = db.query(ScreeningAttempt).filter(
        ScreeningAttempt.survey_id == survey_id,
        ScreeningAttempt.participant_id == participant_id,
        ScreeningAttempt.version == version,
    ).order_by(ScreeningAttempt.completed_at.desc()).first()
    return attempt.status if attempt else "not_started"


def _participation_decision(
    db: Session,
    request: Request,
    *,
    participant: User,
    survey: Survey,
    stage: str,
    response: Optional[Response] = None,
    record_risk_event: bool = True,
):
    """Create one auditable recommendation from the separate trust checks."""
    from app.features.trust.risk import _human_assessment_for_request
    from app.features.verification.service import _verification_satisfies

    human = _human_assessment_for_request(
        db,
        request,
        participant,
        event_type=stage,
        survey_id=survey.id,
        record_event=record_risk_event,
    )
    eligibility_status = _screening_status(db, survey.id, participant.id)
    assurance_status = (
        "passed" if _verification_satisfies(participant, survey, db) else "required"
    )
    quality = None
    if response:
        quality = db.query(ResponseQualityCheck).filter(
            ResponseQualityCheck.response_id == response.id,
        ).order_by(ResponseQualityCheck.created_at.desc()).first()

    quality_status = "not_available"
    quality_score = None
    fraud_risk = False
    if quality:
        quality_status = (
            "needs_review"
            if quality.review_status in {"pending", "needs_review"}
            else quality.quality_label
        )
        quality_score = quality.quality_score
        fraud_risk = bool(quality.fraud_risk)

    inputs = ParticipationInputs(
        human_status=human.status,
        human_risk_score=human.risk_score,
        eligibility_status=eligibility_status,
        assurance_status=assurance_status,
        quality_status=quality_status,
        quality_score=quality_score,
        fraud_risk=fraud_risk,
    )
    recommendation = recommend_participation(inputs, stage=stage)
    approved_questions = _approved_screening_questions(db, survey.id)
    row = ParticipationDecision(
        participant_id=participant.id,
        survey_id=survey.id,
        response_id=response.id if response else None,
        stage=stage,
        human_status=human.status,
        human_risk_score=human.risk_score,
        eligibility_status=eligibility_status,
        assurance_status=assurance_status,
        quality_status=quality_status,
        quality_score=quality_score,
        outcome=recommendation.outcome,
        reasons=[*human.reasons, *recommendation.reasons],
        decision_snapshot={
            "human_reason_count": len(human.reasons),
            "screening_version": (
                approved_questions[0].version
                if approved_questions
                else None
            ),
            "required_assurance": getattr(
                survey, "required_verification_tier", None
            ) or TIER_BASIC,
        },
    )
    db.add(row)
    return recommendation, row


def _study_verification_url(survey: Survey) -> str:
    params = {"needs_tier": survey.required_verification_tier or TIER_BASIC}
    if getattr(survey, "required_email_domains", None):
        params["needs_domains"] = survey.required_email_domains
    return f"/verify?{urlencode(params)}"


def _start_gate_response(recommendation, survey: Survey):
    """Return a safe response for a blocked start, without leaking risk rules."""
    codes = public_reason_codes(recommendation.reasons)
    if "verification_required" in codes:
        return JSONResponse({
            "ok": False,
            "verification_required": True,
            "verification_url": _study_verification_url(survey),
        }, status_code=409)
    return JSONResponse({
        "ok": False,
        "review_required": True,
        "reason": "We need to review this participation attempt before the study can begin.",
    }, status_code=403)


def _screening_continue_url(survey: Survey) -> str:
    from app.features.studies.fields import _survey_external_start_url

    external_url = _survey_external_start_url(survey)
    if external_url:
        return f"/surveys/{survey.id}/start-redirect?{urlencode({'next': external_url})}"
    if getattr(survey, "form_url", None) == "__builtin__":
        return f"/surveys/{survey.id}/take"
    return "/dashboard"


__all__ = ['_approved_screening_questions', '_screening_passed', '_screening_status', '_participation_decision', '_study_verification_url', '_start_gate_response', '_screening_continue_url']
