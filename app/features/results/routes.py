"""Features / results / routes."""

from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import os
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Answer
from app.models import Question
from app.models import Response
from app.models import Survey
from app.models import User


def survey_results_page(
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
    return templates.TemplateResponse("survey_results.html", {
        "request": request,
        "survey": survey,
        "survey_id": survey_id,
        "current_user": current_user
    })


def get_survey_results(
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

    questions = db.query(Question).filter(
        Question.survey_id == survey_id
    ).order_by(Question.order_index).all()

    total_responses = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed"
    ).count()

    result = []
    for q in questions:
        answers = db.query(Answer).join(Response).filter(
            Response.survey_id == survey_id,
            Response.status == "completed",
            Answer.question_id == q.id
        ).all()

        if q.question_type in ("single", "multiple", "dropdown"):
            distribution = {}
            for a in answers:
                val = a.answer_value
                if isinstance(val, list):
                    for v in val:
                        distribution[v] = distribution.get(v, 0) + 1
                else:
                    distribution[str(val)] = distribution.get(str(val), 0) + 1
            result.append({
                "question_id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "total_answers": len(answers),
                "distribution": distribution
            })

        elif q.question_type == "scale":
            values = [a.answer_value for a in answers if a.answer_value is not None]
            avg = round(sum(values) / len(values), 2) if values else 0
            distribution = {str(i): values.count(i) for i in range(1, 6)}
            result.append({
                "question_id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "total_answers": len(answers),
                "average": avg,
                "distribution": distribution
            })

        elif q.question_type == "text":
            result.append({
                "question_id": q.id,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "total_answers": len(answers),
                "responses": [a.answer_value for a in answers]
            })

    return JSONResponse({
        "survey_id": survey_id,
        "survey_title": survey.title,
        "total_responses": total_responses,
        "questions": result
    })


async def analyze_survey(
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

    questions = db.query(Question).filter(
        Question.survey_id == survey_id
    ).order_by(Question.order_index).all()

    answers_data = []
    for q in questions:
        answers = db.query(Answer).join(Response).filter(
            Response.survey_id == survey_id,
            Response.status == "completed",
            Answer.question_id == q.id
        ).all()
        answers_data.append({
            "question": q.question_text,
            "type": q.question_type,
            "answers": [a.answer_value for a in answers]
        })

    total_responses = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed"
    ).count()

    import anthropic, json as _json
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(503, "AI analysis is not available right now.")
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": f"""You are analyzing survey results for a researcher.

Survey title: {survey.title}
Survey description: {survey.description}
Total responses: {total_responses}

Data:
{_json.dumps(answers_data, ensure_ascii=False, indent=2)}

Please provide:
1. Key findings (3-5 bullet points with actual numbers)
2. Notable patterns or trends
3. Text response themes (if any open-ended questions)
4. One recommendation for the researcher

Be concise and specific. Use the actual numbers from the data.
Write in the same language as the survey questions.
"""
        }]
    )

    return JSONResponse({
        "analysis": message.content[0].text,
        "generated_at": datetime.utcnow().isoformat()
    })


__all__ = ['survey_results_page', 'get_survey_results', 'analyze_survey']
