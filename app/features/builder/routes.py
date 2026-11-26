"""Features / builder / routes."""

from app import niche
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Question
from app.models import Survey
from app.models import User


def create_builtin_survey(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.analytics.events import _record_user_event
    from app.features.studies.sharing import _ensure_survey_share_slug

    survey = Survey(
        publisher_id=current_user.id,
        title="Untitled Survey",
        description="",
        form_url="__builtin__",
        task_type="survey",
        category="research",
        estimated_time=10,
        reward_amount=0.0,
        per_person_gross=0.0,
        total_budget=0.0,
        commission_rate=0.15,
        payment_status="unpaid",
        target_responses=50,
        status="draft",
    )
    _ensure_survey_share_slug(db, survey)
    db.add(survey)
    db.commit()
    db.refresh(survey)
    _record_user_event(
        db,
        request,
        "study_created",
        user=current_user,
        target_type="survey",
        target_id=survey.id,
        metadata={"study_title": survey.title, "source": "Builtin builder", "user_role": "researcher"},
    )
    db.commit()
    return RedirectResponse(f"/surveys/{survey.id}/builder", status_code=303)


async def update_survey_info(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from app.features.quality.reviews import _parse_auto_approve_min_score

    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    niche.assert_editable(db, survey)
    body = await request.json()
    financial_fields = {"per_person_gross", "reward_amount", "total_budget", "commission_rate", "target_responses"}
    if (survey.pricing_snapshot or survey.status != "draft") and financial_fields.intersection(body):
        raise HTTPException(400, "Published or quoted study pricing cannot be edited here.")
    survey.title = body.get("title", survey.title)
    survey.description = body.get("description", survey.description)
    survey.category = body.get("category", survey.category)
    survey.estimated_time = body.get("estimated_time", survey.estimated_time)
    survey.per_person_gross = body.get("per_person_gross", survey.per_person_gross)
    survey.reward_amount = body.get("reward_amount", survey.reward_amount)
    survey.total_budget = body.get("total_budget", survey.total_budget)
    survey.commission_rate = body.get("commission_rate", survey.commission_rate)
    survey.target_responses = body.get("target_responses", survey.target_responses)
    if "quality_auto_filter_enabled" in body:
        survey.quality_auto_filter_enabled = bool(body.get("quality_auto_filter_enabled"))
    if "quality_auto_filter_min_score" in body:
        survey.quality_auto_filter_min_score = _parse_auto_approve_min_score(
            body.get("quality_auto_filter_min_score", 80)
        )
    db.commit()
    return JSONResponse({"message": "updated"})


async def add_question(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    request_data = await request.json()

    max_order = db.query(func.max(Question.order_index)).filter(
        Question.survey_id == survey_id
    ).scalar() or 0

    q = Question(
        survey_id=survey_id,
        question_text=request_data.get("question_text", ""),
        question_type=request_data.get("question_type", "single"),
        options=request_data.get("options"),
        is_required=request_data.get("is_required", True),
        order_index=request_data.get("order_index", max_order + 1)
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return JSONResponse({
        "id": q.id,
        "survey_id": q.survey_id,
        "question_text": q.question_text,
        "question_type": q.question_type,
        "options": q.options,
        "is_required": q.is_required,
        "order_index": q.order_index
    })


def get_questions(
    survey_id: int,
    db: Session = Depends(get_db)
):
    questions = db.query(Question).filter(
        Question.survey_id == survey_id
    ).order_by(Question.order_index).all()
    return JSONResponse([{
        "id": q.id,
        "question_text": q.question_text,
        "question_type": q.question_type,
        "options": q.options,
        "is_required": q.is_required,
        "order_index": q.order_index
    } for q in questions])


async def update_question(
    survey_id: int,
    question_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    request_data = await request.json()

    q = db.query(Question).filter(
        Question.id == question_id,
        Question.survey_id == survey_id
    ).first()
    if not q:
        raise HTTPException(404, "Question not found")

    if "question_text" in request_data:
        q.question_text = request_data["question_text"]
    if "question_type" in request_data:
        q.question_type = request_data["question_type"]
    if "options" in request_data:
        q.options = request_data["options"]
    if "is_required" in request_data:
        q.is_required = request_data["is_required"]

    db.commit()
    return JSONResponse({"message": "updated"})


def delete_question(
    survey_id: int,
    question_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    q = db.query(Question).filter(
        Question.id == question_id,
        Question.survey_id == survey_id
    ).first()
    if not q:
        raise HTTPException(404, "Question not found")

    db.delete(q)
    db.commit()
    return JSONResponse({"message": "deleted"})


async def reorder_questions(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    body = await request.json()
    for item in body:
        q = db.query(Question).filter(
            Question.id == item["id"],
            Question.survey_id == survey_id
        ).first()
        if q:
            q.order_index = item["order_index"]

    db.commit()
    return JSONResponse({"message": "reordered"})


def survey_builder(
    survey_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    return templates.TemplateResponse("survey_builder.html", {
        "request": request,
        "survey": survey,
        "survey_id": survey_id,
        "current_user": current_user
    })


__all__ = ['create_builtin_survey', 'update_survey_info', 'add_question', 'get_questions', 'update_question', 'delete_question', 'reorder_questions', 'survey_builder']
