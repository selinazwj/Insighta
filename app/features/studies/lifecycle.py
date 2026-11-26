"""Features / studies / lifecycle."""

from app import niche
from datetime import datetime
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.ai_growth.models import JumpEvent
from app.ai_growth.models import RespondentPrediction
from app.ai_growth.models import SurveySegmentStats
from app.ai_growth.models import UserActivityEvent
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Answer
from app.models import GoogleCalendarEvent
from app.models import Notification
from app.models import ParticipationDecision
from app.models import Question
from app.models import Response
from app.models import ResponseQualityCheck
from app.models import RiskSignalEvent
from app.models import ScreeningAttempt
from app.models import ScreeningQuestion
from app.models import StudySettlement
from app.models import Survey
from app.models import User
from app.settlements import ensure_settlement
from app.settlements import lock_study
from app.settlements import settlement_payload
from app.wallet_models import WalletRefundPart


def _delete_survey_tree(db: Session, survey: Survey) -> None:
    niche.assert_editable(db, survey)
    if survey.pricing_snapshot and (survey.payment_status == "paid" or db.query(StudySettlement).filter_by(survey_id=survey.id).first()):
        raise HTTPException(409, "Funded studies must be closed and kept for settlement records.")
    survey_id = survey.id
    # Delete dependent rows first. Postgres/Supabase enforces these foreign keys,
    # so deleting the survey directly can fail once quality/AI tracking exists.
    question_ids = [q.id for q in db.query(Question).filter(Question.survey_id == survey_id).all()]
    response_ids = [row[0] for row in db.query(Response.id).filter(Response.survey_id == survey_id).all()]
    if question_ids:
        db.query(Answer).filter(Answer.question_id.in_(question_ids)).delete(synchronize_session=False)
    if response_ids:
        db.query(GoogleCalendarEvent).filter(GoogleCalendarEvent.response_id.in_(response_ids)).delete(synchronize_session=False)
        db.query(Answer).filter(Answer.response_id.in_(response_ids)).delete(synchronize_session=False)
        db.query(JumpEvent).filter(JumpEvent.response_id.in_(response_ids)).delete(synchronize_session=False)
        db.query(ResponseQualityCheck).filter(ResponseQualityCheck.response_id.in_(response_ids)).delete(synchronize_session=False)
    db.query(JumpEvent).filter(JumpEvent.survey_id == survey_id).delete(synchronize_session=False)
    db.query(RespondentPrediction).filter(RespondentPrediction.survey_id == survey_id).delete(synchronize_session=False)
    db.query(SurveySegmentStats).filter(SurveySegmentStats.survey_id == survey_id).delete(synchronize_session=False)
    db.query(UserActivityEvent).filter(UserActivityEvent.survey_id == survey_id).delete(synchronize_session=False)
    db.query(ResponseQualityCheck).filter(ResponseQualityCheck.survey_id == survey_id).delete(synchronize_session=False)
    db.query(ParticipationDecision).filter(ParticipationDecision.survey_id == survey_id).delete(synchronize_session=False)
    db.query(RiskSignalEvent).filter(RiskSignalEvent.survey_id == survey_id).delete(synchronize_session=False)
    db.query(ScreeningAttempt).filter(ScreeningAttempt.survey_id == survey_id).delete(synchronize_session=False)
    db.query(ScreeningQuestion).filter(ScreeningQuestion.survey_id == survey_id).delete(synchronize_session=False)
    db.query(Question).filter(Question.survey_id == survey_id).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.survey_id == survey_id).delete(synchronize_session=False)
    db.query(Response).filter(Response.survey_id == survey_id).delete(synchronize_session=False)
    db.delete(survey)
    db.commit()


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

    _delete_survey_tree(db, survey)
    return RedirectResponse("/publisher", status_code=303)


def publish_existing_survey(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event

    s = db.query(Survey).filter(Survey.id == survey_id, Survey.publisher_id == current_user.id).first()
    if not s: raise HTTPException(404, "Survey not found")
    if s.pricing_snapshot and s.status == 'closed':
        raise HTTPException(409, 'A funded closed study cannot be republished. Create a new listing.')
    if getattr(s, 'payment_status', 'unpaid') != 'paid': raise HTTPException(400, "Survey must be paid before publishing")
    if s.status != "published":
        s.status = "published"
        s.published_at = datetime.utcnow()
        s.closed_at = None
        _record_user_event(
            db,
            request,
            "study_published",
            user=current_user,
            target_type="survey",
            target_id=s.id,
            metadata={"study_title": s.title, "source": "Publisher", "user_role": "researcher"},
        )
    db.commit()
    return RedirectResponse("/publisher", status_code=303)


def close_existing_survey(survey_id: int, close_reason: str = Form("completed"),
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = lock_study(db, survey_id)
    if not s or s.publisher_id != current_user.id:
        raise HTTPException(404, "Survey not found")
    if niche.request_for(db, s.id) and s.payment_status != 'paid':
        raise HTTPException(409, 'This niche request has not been funded. Contact our founder to change its scope.')
    if close_reason not in {"completed", "researcher_cancelled"}:
        raise HTTPException(400, "Choose close study or researcher cancellation.")
    if close_reason == "researcher_cancelled" and s.pricing_snapshot and s.pricing_snapshot["study_type"] not in {"interview", "in_person"}:
        raise HTTPException(400, "The cancellation recruitment-fee rule applies only to live studies.")
    if s.status != "closed":
        s.status, s.closed_at, s.close_reason = "closed", datetime.utcnow(), close_reason
    ensure_settlement(db, s)
    db.commit()
    return RedirectResponse("/publisher", status_code=303)


def reopen_closed_survey(survey_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.query(Survey).filter(Survey.id == survey_id, Survey.publisher_id == current_user.id).first()
    if not s: raise HTTPException(404, "Survey not found")
    if db.query(StudySettlement).filter_by(survey_id=s.id).first():
        raise HTTPException(409, "A study in settlement cannot reopen. Create a new listing.")
    if s.payment_status not in {"paid", "admin_demo"}:
        raise HTTPException(400, "Study must be funded before reopening.")
    if s.status == "closed": s.status = "published"; s.closed_at = None
    db.commit()
    return RedirectResponse("/publisher", status_code=303)


def study_settlement_status(survey_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    study = db.query(Survey).filter_by(id=survey_id, publisher_id=current_user.id).first()
    if not study:
        raise HTTPException(404, "Study not found")
    return {"settlement": settlement_payload(db.query(StudySettlement).filter_by(survey_id=study.id).first())}


def retry_study_settlement(survey_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    study = lock_study(db, survey_id)
    if not study or study.publisher_id != current_user.id:
        raise HTTPException(404, "Study not found")
    row = db.query(StudySettlement).filter_by(survey_id=study.id).first()
    if not row or row.status not in {"retrying", "manual_review"}:
        raise HTTPException(409, "This settlement is not awaiting a refund retry.")
    if row.stripe_refund_id or db.query(WalletRefundPart).filter(
        WalletRefundPart.settlement_id == row.id, WalletRefundPart.status == "manual_review").first():
        raise HTTPException(409, "An existing refund needs support reconciliation; it will not be submitted twice.")
    row.status, row.attempts, row.next_attempt_at = "ready", 0, datetime.utcnow()
    db.commit()
    return {"ok": True, "message": "Refund reconciliation queued."}


__all__ = ['_delete_survey_tree', 'delete_survey', 'publish_existing_survey', 'close_existing_survey', 'reopen_closed_survey', 'study_settlement_status', 'retry_study_settlement']
