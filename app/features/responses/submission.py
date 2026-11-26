"""Features / responses / submission."""

from datetime import datetime
from datetime import timezone
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from urllib.parse import urlencode
from app.ai_growth.jump import mark_latest_jump_completed_for_response
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Answer
from app.models import Response
from app.models import Survey
from app.models import User
from app.payouts import mark_response_under_review
from app.quality_engine import evaluate_builtin_response
from app.quality_engine import upsert_builtin_quality_check
from app.settlements import assert_study_open
from app.trust_engine import BLOCK as TRUST_BLOCK
from app.trust_engine import public_reason_codes
from app.verification.assurance import TIER_BASIC


def survey_take(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.screening.service import _participation_decision
    from app.features.screening.service import _screening_passed

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.status == "published"
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    if not _screening_passed(db, survey_id, current_user.id):
        return RedirectResponse(f"/surveys/{survey_id}/screening", status_code=303)
    trust_recommendation, _ = _participation_decision(
        db,
        request,
        participant=current_user,
        survey=survey,
        stage="start",
        record_risk_event=False,
    )
    db.commit()
    if trust_recommendation.outcome == TRUST_BLOCK:
        codes = public_reason_codes(trust_recommendation.reasons)
        if "verification_required" in codes:
            return RedirectResponse(
                f"/verify?{urlencode({'needs_tier': survey.required_verification_tier or TIER_BASIC})}",
                status_code=303,
            )
        return RedirectResponse("/participation-review", status_code=303)
    return templates.TemplateResponse("survey_take.html", {
        "request": request,
        "survey": survey,
        "current_user": current_user
    })


async def submit_answers(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.participation.completion import _notify_publisher_response_ready
    from app.features.participation.completion import _verification_response_fields
    from app.features.screening.service import _participation_decision
    from app.features.screening.service import _screening_passed
    from app.features.screening.service import _start_gate_response
    from app.features.trust.risk import _extract_client_meta
    from app.features.trust.risk import _privacy_ref

    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    assert_study_open(db, survey)
    if survey.status != "published":
        raise HTTPException(400, "Survey not published")
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

    body = await request.json()
    meta = _extract_client_meta(request, current_user)
    stored_network_ref = _privacy_ref("response_network", meta["client_ip"])
    stored_device_ref = _privacy_ref("response_device", meta["device_fingerprint"])

    r = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.participant_id == current_user.id
    ).first()
    if not r:
        r = Response(
            survey_id=survey_id,
            participant_id=current_user.id,
            status="started",
            client_ip=stored_network_ref,
            user_agent=meta["user_agent"],
            device_fingerprint=stored_device_ref,
        )
        db.add(r)
        db.flush()
    else:
        r.client_ip = stored_network_ref
        r.user_agent = meta["user_agent"]
        r.device_fingerprint = stored_device_ref

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
        # bell notification + publisher email, shared with the external path
        _notify_publisher_response_ready(db, survey, current_user)

    quality_payload = None
    try:
        # Response completion must not silently spend LLM tokens. The standard
        # rule/anomaly quality score runs locally; AI analysis remains available
        # only through explicit AI actions.
        quality_result = evaluate_builtin_response(db, survey_id=survey_id, response_id=r.id, run_llm=False)
        upsert_builtin_quality_check(db, survey_id=survey_id, response_id=r.id, result=quality_result)
        quality_payload = {
            "score": quality_result.quality_score,
            "label": quality_result.quality_label,
            "fraud_risk": quality_result.fraud_risk,
            "reasons": quality_result.reasons,
        }
    except Exception as exc:
        print(f"Quality check failed for response {r.id}: {exc}")

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
    mark_latest_jump_completed_for_response(db, r)

    # verify nudge fields — same check as /complete, for built-in surveys
    body = {"message": "submitted successfully", **_verification_response_fields(current_user, survey, db)}
    if quality_payload:
        body["quality"] = quality_payload
    body["trust_review"] = {
        "status": completion_recommendation.outcome,
        "reason_codes": public_reason_codes(completion_recommendation.reasons),
    }
    return JSONResponse(body)


__all__ = ['survey_take', 'submit_answers']
