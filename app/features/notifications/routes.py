"""Features / notifications / routes."""

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Notification
from app.models import ParticipationDecision
from app.models import Response
from app.models import ResponseQualityCheck
from app.models import Survey
from app.models import User
from app.payouts import reject_response_payout
from app.payouts import release_response_payout
from app.trust_engine import public_reason_codes
from app.verification.assurance import TIER_IDENTITY
from app.verification.assurance import TIER_TRUSTED


def get_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.studies.fields import _normalize_task_type
    from app.features.studies.fields import _task_type_label
    from app.features.verification.service import _current_assurance_tier
    from app.features.verification.service import _normalize_domains
    from app.features.verification.service import _verification_satisfies

    notifs = db.query(Notification).filter(
        Notification.publisher_id == current_user.id
    ).order_by(Notification.created_at.desc()).all()

    # ive added this for verification — tell the researcher if the participant is verified
    # so they can decide before approving payout (we warn but still allow)
    # phase 3 — tier aware; email-domain feature — also domain aware, all via the helper
    # ive optimized this — batch-load the surveys and participants in two queries
    # instead of two queries PER notification (was an N+1)
    survey_ids = {n.survey_id for n in notifs if n.survey_id}
    participant_ids = {n.participant_id for n in notifs if n.participant_id}
    surveys_by_id = {s.id: s for s in db.query(Survey).filter(Survey.id.in_(survey_ids)).all()} if survey_ids else {}
    users_by_id = {u.id: u for u in db.query(User).filter(User.id.in_(participant_ids)).all()} if participant_ids else {}

    def _verify_info(n):
        survey = surveys_by_id.get(n.survey_id)
        participant = users_by_id.get(n.participant_id)
        required_tier = survey.required_verification_tier if survey else None
        domains = _normalize_domains(getattr(survey, "required_email_domains", None) if survey else None)
        requires = required_tier in {TIER_IDENTITY, TIER_TRUSTED} or bool(domains)
        status = getattr(participant, "verification_status", "unverified") or "unverified"
        participant_tier = _current_assurance_tier(db, participant) if participant else None
        satisfied = bool(survey and participant and _verification_satisfies(participant, survey, db))
        return requires, status, participant_tier, required_tier, domains, satisfied

    payload = []
    for n in notifs:
        requires_verification, verification_status, participant_tier, required_tier, domains, satisfied = _verify_info(n)
        payload.append({
            "id": n.id,
            "participant_id": n.participant_id,
            "participant_email": n.participant_email,
            "survey_title": n.survey_title,
            "task_type": _normalize_task_type(n.task_type),
            "task_type_label": _task_type_label(n.task_type),
            "status": n.status,
            "created_at": n.created_at.strftime("%b %d, %H:%M") if n.created_at else "",
            "requires_verification": requires_verification,
            "participant_verification_status": verification_status,
            "participant_verified_tier": participant_tier,
            "required_verification_tier": required_tier,
            "required_email_domains": ", ".join(domains) if domains else "",
            "verification_warning": requires_verification and not satisfied,
        })
    return JSONResponse(payload)


def accept_notification(
    notif_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event
    from app.features.messaging.delivery import send_email
    from app.features.screening.service import _participation_decision

    notif = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.publisher_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(404, "Notification not found")

    response = db.query(Response).filter(
        Response.survey_id == notif.survey_id,
        Response.participant_id == notif.participant_id,
    ).first()
    if response and response.status != "completed":
        raise HTTPException(
            409,
            "The study must be confirmed complete before payout approval.",
        )
    notif.status = "accepted"
    if response:
        survey = db.query(Survey).filter(Survey.id == notif.survey_id).first()
        participant = db.query(User).filter(User.id == notif.participant_id).first()
        payout_recommendation = None
        if survey and participant:
            payout_recommendation, _ = _participation_decision(
                db,
                request,
                participant=participant,
                survey=survey,
                stage="payout",
                response=response,
                record_risk_event=False,
            )
        # This click is the human review/override. The automated recommendation
        # is recorded, but it does not silently reject an otherwise valid person.
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
                    "participant_id": notif.participant_id,
                    "participant_email": notif.participant_email,
                    "trust_recommendation": (
                        payout_recommendation.outcome
                        if payout_recommendation else "not_available"
                    ),
                    "source": "Notification",
                    "user_role": "researcher",
                },
            )
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


def reject_notification(
    notif_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event

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
    survey = db.query(Survey).filter(Survey.id == notif.survey_id).first()
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
                "participant_id": notif.participant_id,
                "participant_email": notif.participant_email,
                "source": "Notification",
                "user_role": "researcher",
            },
        )
    db.commit()
    return {"message": "rejected"}


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
        trust = db.query(ParticipationDecision).filter(
            ParticipationDecision.response_id == r.id,
        ).order_by(ParticipationDecision.created_at.desc()).first()
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
            "trust_outcome": trust.outcome if trust else None,
            "human_status": trust.human_status if trust else None,
            "eligibility_status": trust.eligibility_status if trust else None,
            "assurance_status": trust.assurance_status if trust else None,
            "trust_reason_codes": public_reason_codes(trust.reasons or []) if trust else [],
        })
    return JSONResponse(result)


__all__ = ['get_notifications', 'accept_notification', 'reject_notification', 'get_pending_responses']
