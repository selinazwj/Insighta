"""Features / screening / routes."""

from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.bootstrap.runtime import templates
from app.bot_defense import check_rate_limit
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import ParticipationDecision
from app.models import ScreeningAttempt
from app.models import ScreeningQuestion
from app.models import Survey
from app.models import User
from app.screening_engine import evaluate_screening
from app.screening_engine import generate_screening_draft
from app.screening_engine import validate_researcher_edit
from app.trust_engine import public_reason_codes


def get_screening_configuration(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    rows = db.query(ScreeningQuestion).filter(
        ScreeningQuestion.survey_id == survey_id,
    ).order_by(ScreeningQuestion.version.desc(), ScreeningQuestion.order_index.asc()).all()
    attempt_rows = db.query(
        ScreeningAttempt.version,
        ScreeningAttempt.status,
        func.count(ScreeningAttempt.id),
    ).filter(
        ScreeningAttempt.survey_id == survey_id,
    ).group_by(ScreeningAttempt.version, ScreeningAttempt.status).all()
    stats_by_version: dict[int, dict[str, int]] = {}
    for version, status, count in attempt_rows:
        version_stats = stats_by_version.setdefault(int(version), {
            "total": 0, "passed": 0, "failed": 0, "review": 0,
        })
        version_stats["total"] += int(count or 0)
        if status in version_stats:
            version_stats[status] += int(count or 0)
    return JSONResponse({
        "survey_id": survey_id,
        "versions": [{
            "version": version,
            **counts,
            "pass_rate": round(
                counts["passed"] / counts["total"] * 100, 1
            ) if counts["total"] else None,
        } for version, counts in sorted(stats_by_version.items(), reverse=True)],
        "questions": [{
            "id": row.id,
            "version": row.version,
            "attribute": row.attribute,
            "prompt": row.prompt,
            "question_type": row.question_type,
            "options": row.options or [],
            "accepted_answers": row.accepted_answers or [],
            "rationale": row.rationale,
            "sensitivity": row.sensitivity,
            "source": row.source,
            "status": row.status,
            "order_index": row.order_index,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        } for row in rows],
    })


def get_survey_trust_summary(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    grouped = db.query(
        ParticipationDecision.stage,
        ParticipationDecision.outcome,
        func.count(ParticipationDecision.id),
    ).filter(
        ParticipationDecision.survey_id == survey_id,
    ).group_by(
        ParticipationDecision.stage,
        ParticipationDecision.outcome,
    ).all()
    by_stage: dict[str, dict[str, int]] = {}
    for stage, outcome, count in grouped:
        by_stage.setdefault(stage, {"allow": 0, "review": 0, "block": 0})
        by_stage[stage][outcome] = int(count or 0)

    latest_decisions = db.query(ParticipationDecision).filter(
        ParticipationDecision.survey_id == survey_id,
    ).order_by(ParticipationDecision.created_at.desc()).limit(100).all()
    return JSONResponse({
        "survey_id": survey_id,
        "by_stage": by_stage,
        "recent": [{
            "stage": row.stage,
            "outcome": row.outcome,
            "human_status": row.human_status,
            "eligibility_status": row.eligibility_status,
            "assurance_status": row.assurance_status,
            "quality_status": row.quality_status,
            "reason_codes": public_reason_codes(row.reasons or []),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in latest_decisions],
    })


def create_screening_draft(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    if not check_rate_limit(
        f"screening-draft:user:{current_user.id}",
        max_hits=10,
        window_seconds=3600,
    ):
        raise HTTPException(429, "Too many screening drafts. Please try again later.")

    # This endpoint is reached only from the explicit “Generate draft” action.
    draft = generate_screening_draft(survey, use_ai=True)
    if not draft:
        raise HTTPException(
            400,
            "Add at least one supported targeting requirement before generating a screener.",
        )
    latest_version = db.query(func.max(ScreeningQuestion.version)).filter(
        ScreeningQuestion.survey_id == survey_id,
    ).scalar() or 0
    next_version = latest_version + 1
    db.query(ScreeningQuestion).filter(
        ScreeningQuestion.survey_id == survey_id,
        ScreeningQuestion.status == "draft",
    ).update({"status": "retired"}, synchronize_session=False)
    for index, item in enumerate(draft):
        db.add(ScreeningQuestion(
            survey_id=survey_id,
            version=next_version,
            attribute=item["attribute"],
            prompt=item["prompt"],
            question_type=item["question_type"],
            options=item["options"],
            accepted_answers=item["accepted_answers"],
            rationale=item.get("rationale"),
            sensitivity="low",
            source=item.get("source", "rules"),
            status="draft",
            order_index=index,
        ))
    _record_user_event(
        db,
        request,
        "screening_draft_created",
        user=current_user,
        target_type="survey",
        target_id=survey.id,
        metadata={
            "version": next_version,
            "question_count": len(draft),
            "user_role": "researcher",
        },
    )
    db.commit()
    return JSONResponse({
        "ok": True,
        "version": next_version,
        "question_count": len(draft),
        "status": "draft",
        "message": "Draft created. Review and approve it before participants see it.",
    })


async def update_screening_draft(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    body = await request.json()
    try:
        version = int(body.get("version"))
    except (TypeError, ValueError):
        raise HTTPException(400, "A valid draft version is required.")
    rows = db.query(ScreeningQuestion).filter(
        ScreeningQuestion.survey_id == survey_id,
        ScreeningQuestion.version == version,
        ScreeningQuestion.status == "draft",
    ).order_by(ScreeningQuestion.order_index.asc()).all()
    if not rows:
        raise HTTPException(409, "That draft is no longer editable. Refresh and try again.")
    baseline = [{
        "attribute": row.attribute,
        "prompt": row.prompt,
        "question_type": row.question_type,
        "options": row.options or [],
        "accepted_answers": row.accepted_answers or [],
        "rationale": row.rationale,
        "source": row.source,
        "sensitivity": row.sensitivity,
    } for row in rows]
    try:
        edited = validate_researcher_edit(body.get("questions"), baseline)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    rows_by_attribute = {row.attribute: row for row in rows}
    for item in edited:
        row = rows_by_attribute[item["attribute"]]
        row.prompt = item["prompt"]
        row.options = item["options"]
        row.order_index = item["order_index"]
        row.source = item["source"]
        row.updated_at = datetime.utcnow()
    _record_user_event(
        db,
        request,
        "screening_draft_updated",
        user=current_user,
        target_type="survey",
        target_id=survey.id,
        metadata={
            "version": version,
            "question_count": len(rows),
            "user_role": "researcher",
        },
    )
    db.commit()
    return JSONResponse({
        "ok": True,
        "version": version,
        "question_count": len(rows),
        "status": "draft",
    })


async def approve_screening_draft(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    requested_version = body.get("version") if isinstance(body, dict) else None
    if requested_version is not None:
        try:
            latest_version = int(requested_version)
        except (TypeError, ValueError):
            raise HTTPException(400, "A valid draft version is required.")
    else:
        latest_version = db.query(func.max(ScreeningQuestion.version)).filter(
            ScreeningQuestion.survey_id == survey_id,
            ScreeningQuestion.status == "draft",
        ).scalar()
    if not latest_version:
        raise HTTPException(400, "No screening draft is waiting for approval.")
    rows = db.query(ScreeningQuestion).filter(
        ScreeningQuestion.survey_id == survey_id,
        ScreeningQuestion.version == latest_version,
        ScreeningQuestion.status == "draft",
    ).all()
    if not rows:
        raise HTTPException(400, "The screening draft is empty.")
    db.query(ScreeningQuestion).filter(
        ScreeningQuestion.survey_id == survey_id,
        ScreeningQuestion.status == "approved",
    ).update({"status": "retired"}, synchronize_session=False)
    for row in rows:
        row.status = "approved"
        row.approved_at = datetime.utcnow()
        row.approved_by = current_user.id
        row.updated_at = datetime.utcnow()
    _record_user_event(
        db,
        request,
        "screening_version_approved",
        user=current_user,
        target_type="survey",
        target_id=survey.id,
        metadata={
            "version": latest_version,
            "question_count": len(rows),
            "user_role": "researcher",
        },
    )
    db.commit()
    return JSONResponse({
        "ok": True,
        "version": latest_version,
        "question_count": len(rows),
        "status": "approved",
    })


def participant_screening_page(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.screening.service import _approved_screening_questions
    from app.features.screening.service import _screening_continue_url
    from app.web.middleware import no_store_response

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.status == "published",
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    questions = _approved_screening_questions(db, survey_id)
    if not questions:
        return RedirectResponse(_screening_continue_url(survey), status_code=303)
    version = questions[0].version
    attempt = db.query(ScreeningAttempt).filter(
        ScreeningAttempt.survey_id == survey_id,
        ScreeningAttempt.participant_id == current_user.id,
        ScreeningAttempt.version == version,
    ).order_by(ScreeningAttempt.started_at.desc()).first()
    return no_store_response(templates.TemplateResponse("screening.html", {
        "request": request,
        "current_user": current_user,
        "survey": survey,
        "questions": questions,
        "attempt": attempt,
        "continue_url": _screening_continue_url(survey),
    }))


async def submit_participant_screening(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event
    from app.features.screening.service import _approved_screening_questions
    from app.features.screening.service import _screening_continue_url

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.status == "published",
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    questions = _approved_screening_questions(db, survey_id)
    if not questions:
        return RedirectResponse(_screening_continue_url(survey), status_code=303)
    version = questions[0].version
    existing = db.query(ScreeningAttempt).filter(
        ScreeningAttempt.survey_id == survey_id,
        ScreeningAttempt.participant_id == current_user.id,
        ScreeningAttempt.version == version,
        ScreeningAttempt.status.in_(["passed", "failed", "review"]),
    ).order_by(ScreeningAttempt.started_at.desc()).first()
    if existing:
        return RedirectResponse(f"/surveys/{survey_id}/screening", status_code=303)
    if not check_rate_limit(
        f"screening-submit:user:{current_user.id}:survey:{survey_id}",
        max_hits=3,
        window_seconds=3600,
    ):
        raise HTTPException(429, "Too many screening attempts. Please try again later.")

    form = await request.form()
    answers = {
        row.attribute: (form.get(f"answer_{row.id}") or "").strip()
        for row in questions
    }
    decision = evaluate_screening([{
        "attribute": row.attribute,
        "question_type": row.question_type,
        "accepted_answers": row.accepted_answers or [],
    } for row in questions], answers)
    attempt = ScreeningAttempt(
        survey_id=survey_id,
        participant_id=current_user.id,
        version=version,
        answers=answers,
        status=decision.status,
        risk_score=decision.risk_score,
        # Keep detailed mismatch reasons internal; the participant sees only the
        # generic outcome so the accepted criteria cannot be learned by retries.
        reasons=list(decision.reasons),
        completed_at=datetime.utcnow(),
    )
    db.add(attempt)
    _record_user_event(
        db,
        request,
        "screening_completed",
        user=current_user,
        target_type="survey",
        target_id=survey.id,
        metadata={
            "version": version,
            "outcome": decision.status,
            "user_role": "participant",
        },
    )
    db.commit()
    if decision.status == "passed":
        return RedirectResponse(_screening_continue_url(survey), status_code=303)
    return RedirectResponse(f"/surveys/{survey_id}/screening", status_code=303)


__all__ = ['get_screening_configuration', 'get_survey_trust_summary', 'create_screening_draft', 'update_screening_draft', 'approve_screening_draft', 'participant_screening_page', 'submit_participant_screening']
