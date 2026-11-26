"""Features / participation / completion."""

from datetime import datetime
from datetime import timezone
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Notification
from app.models import Response
from app.models import Survey
from app.models import User
from app.payouts import mark_response_under_review
from app.payouts import return_response_to_review
from app.settlements import assert_study_open
from app.settlements import lock_study
from app.trust_engine import BLOCK as TRUST_BLOCK
from app.trust_engine import public_reason_codes


def _notify_publisher_response_ready(db: Session, survey: Survey, participant: User) -> None:
    """
    ive extracted this — both completion paths (external /complete and builtin
    /submit) used to carry an identical 45-line copy of this. one place now:
    creates the researchers bell notification (deduped) + sends them an email.
    """
    from app.features.messaging.delivery import send_email
    from app.features.studies.fields import _uses_booking_flow

    existing_notif = db.query(Notification).filter(
        Notification.survey_id == survey.id,
        Notification.participant_id == participant.id,
        Notification.status == "pending",
    ).first()
    if not existing_notif:
        db.add(Notification(
            publisher_id=survey.publisher_id,
            participant_id=participant.id,
            survey_id=survey.id,
            participant_email=participant.email,
            survey_title=survey.title,
            task_type=getattr(survey, "task_type", "survey") or "survey",
            status="pending",
        ))

    publisher = db.query(User).filter(User.id == survey.publisher_id).first()
    if publisher and publisher.email:
        is_live_study = _uses_booking_flow(getattr(survey, "task_type", None))
        completion_label = "live interview" if is_live_study else "survey"
        send_email(
            to=publisher.email,
            subject=f"[Insighta] {completion_label.title()} ready for payout review: {survey.title}",
            body=f"""
            <div style="font-family: sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a18;">
              <h2 style="font-size: 22px; margin-bottom: 8px;">📋 Response Ready for Review</h2>
              <p style="color: #8a8a82; margin-bottom: 24px;">A participant has completed your {completion_label} and is awaiting your payout approval.</p>
              <div style="background: #f3f1ea; border-radius: 10px; padding: 20px 24px; margin-bottom: 24px;">
                <div style="font-size: 13px; color: #8a8a82; margin-bottom: 4px;">Survey</div>
                <div style="font-size: 17px; font-weight: 600; margin-bottom: 12px;">{survey.title}</div>
                <div style="font-size: 13px; color: #8a8a82; margin-bottom: 4px;">Participant</div>
                <div style="font-size: 15px;">{participant.email}</div>
                <div style="font-size: 13px; color: #8a8a82; margin-top: 12px; margin-bottom: 4px;">Reward</div>
                <div style="font-size: 15px; font-weight: 600; color: #2d6a4f;">${survey.reward_amount:.2f}</div>
              </div>
              <a href="https://insightaco.org/publisher" style="display: inline-block; padding: 12px 24px; background: #2d6a4f; color: white; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px;">
                Review & Approve →
              </a>
            </div>
            """
        )


def _verification_response_fields(user: User, survey: Survey, db: Session) -> dict:
    """ive extracted this — the completion JSON both handlers return for the verify nudge"""
    from app.features.verification.service import _verification_satisfies

    return {
        "needs_verification": not _verification_satisfies(user, survey, db),
        "required_verification_tier": getattr(survey, "required_verification_tier", None) or "tier_3",
        "required_email_domains": getattr(survey, "required_email_domains", None) or "",
    }


def complete_survey(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.analytics.events import _record_user_event
    from app.features.screening.service import _participation_decision
    from app.features.screening.service import _screening_passed
    from app.features.screening.service import _start_gate_response
    from app.features.studies.fields import _normalize_task_type
    from app.features.studies.fields import _uses_booking_flow

    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey: raise HTTPException(404, "Survey not found")
    if survey.status != "published": raise HTTPException(400, "Survey not published")
    assert_study_open(db, survey)
    if _uses_booking_flow(getattr(survey, "task_type", None)):
        return JSONResponse({
            "ok": False,
            "interview_confirmation_required": True,
            "detail": "The researcher confirms completion after the live interview.",
        }, status_code=409)
    if not _screening_passed(db, survey_id, current_user.id):
        return JSONResponse({
            "ok": False,
            "screening_required": True,
            "screening_url": f"/surveys/{survey_id}/screening",
        }, status_code=409)
    trust_recommendation, _ = _participation_decision(
        db,
        request,
        participant=current_user,
        survey=survey,
        stage="completion",
    )
    if trust_recommendation.outcome == TRUST_BLOCK:
        db.commit()
        return _start_gate_response(trust_recommendation, survey)

    r = db.query(Response).filter(
        Response.survey_id == survey_id, Response.participant_id == current_user.id
    ).first()

    if not r:
        r = Response(survey_id=survey_id, participant_id=current_user.id, status="started")
        db.add(r)
        db.flush()

    if r.status != "completed":
        r.status = "completed"
        r.completed_at = datetime.now(timezone.utc)
        r.payout_amount = survey.reward_amount
        mark_response_under_review(r)
        # bell notification + publisher email, shared with the builtin path
        _notify_publisher_response_ready(db, survey, current_user)
        _record_user_event(
            db,
            request,
            "study_complete",
            user=current_user,
            target_type="survey",
            target_id=survey.id,
            page_path=f"/surveys/{survey.id}/take",
            metadata={
                "study_title": survey.title,
                "task_type": _normalize_task_type(getattr(survey, "task_type", None)),
                "reward_amount": survey.reward_amount,
                "source": "Survey",
                "user_role": "participant",
            },
        )

    completion_recommendation, _ = _participation_decision(
        db,
        request,
        participant=current_user,
        survey=survey,
        stage="completion",
        response=r,
        record_risk_event=False,
    )
    db.commit()

    # verify nudge fields — tell the frontend to send the participant to /verify
    # if this survey needs a tier or email domain they dont have yet
    return JSONResponse({
        "message": "completed",
        **_verification_response_fields(current_user, survey, db),
        "trust_review": {
            "status": completion_recommendation.outcome,
            "reason_codes": public_reason_codes(completion_recommendation.reasons),
        },
    })


def mark_booking_no_show(survey_id: int, response_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.features.studies.fields import _uses_booking_flow

    study = lock_study(db, survey_id)
    if not study or study.publisher_id != current_user.id:
        raise HTTPException(404, "Study not found")
    if not _uses_booking_flow(study.task_type):
        raise HTTPException(400, "No-show replacement applies to live studies.")
    assert_study_open(db, study)
    response = db.query(Response).filter_by(id=response_id, survey_id=study.id).first()
    if not response:
        raise HTTPException(404, "Booking not found")
    if response.status == "completed" or response.payout_status in {"approved", "paid"}:
        raise HTTPException(409, "Completed participation cannot be marked as a no-show.")
    response.status, response.payout_status, response.payout_amount = "no_show", "not_eligible", 0
    db.commit()
    return {"ok": True, "message": "No-show recorded. Recruit a replacement within the existing participant cap; no new recruitment fee is charged."}


def confirm_live_interview_complete(
    survey_id: int,
    response_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event
    from app.features.studies.fields import _uses_booking_flow

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Study not found")
    assert_study_open(db, survey)
    if not _uses_booking_flow(getattr(survey, "task_type", None)):
        raise HTTPException(400, "This action is only available for live studies.")
    response = db.query(Response).filter(
        Response.id == response_id,
        Response.survey_id == survey_id,
    ).first()
    if not response:
        raise HTTPException(404, "Booking not found")
    participant = db.query(User).filter(User.id == response.participant_id).first()
    if not participant:
        raise HTTPException(404, "Participant not found")

    if response.status != "completed":
        response.status = "completed"
        response.completed_at = datetime.now(timezone.utc)
        response.payout_amount = survey.reward_amount
        mark_response_under_review(response)
        _notify_publisher_response_ready(db, survey, participant)
        _record_user_event(
            db,
            request,
            "live_interview_completed",
            user=current_user,
            target_type="survey",
            target_id=survey.id,
            metadata={
                "study_title": survey.title,
                "participant_id": participant.id,
                "source": "Researcher schedule",
                "user_role": "researcher",
            },
        )
        db.commit()

    return {
        "ok": True,
        "status": response.status,
        "payout_status": response.payout_status,
        "message": "Interview marked complete and sent to payout review.",
    }


def modify_completed_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.studies.fields import _uses_booking_flow

    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if survey and _uses_booking_flow(getattr(survey, "task_type", None)):
        return JSONResponse({
            "detail": "Live interview completion is managed by the researcher."
        }, status_code=409)
    r = db.query(Response).filter(
        Response.survey_id == survey_id, Response.participant_id == current_user.id
    ).first()

    if r and r.status == "completed":
        if survey:
            assert_study_open(db, survey)

        return_response_to_review(db, r)
        # The payout helper refreshes the row under its account/review locks.
        # Apply the participation reset afterwards so that refresh cannot discard
        # these state changes on legacy studies without a pricing snapshot.
        r.status = "started"; r.completed_at = None
        # ive added this — undoing a completion removes the pending bell notification,
        # otherwise the researcher could approve a payout for a response thats no
        # longer completed
        db.query(Notification).filter(
            Notification.survey_id == survey_id,
            Notification.participant_id == current_user.id,
            Notification.status == "pending",
        ).delete()
        db.commit()
        return {"message": "Response modified"}

    return JSONResponse({"detail": "Response not found or not completed"}, status_code=404)


__all__ = ['_notify_publisher_response_ready', '_verification_response_fields', 'complete_survey', 'mark_booking_no_show', 'confirm_live_interview_complete', 'modify_completed_survey']
