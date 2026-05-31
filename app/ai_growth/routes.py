"""FastAPI routes for AI respondent prediction and one-click jump."""

from __future__ import annotations

from typing import Optional
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import engine, get_db
from app.models import Base, Survey, User
from app.ai_growth import models as _ai_models  # noqa: F401 - registers additive tables in Base.metadata
from app.ai_growth.jump import complete_response_with_token, mark_returned, start_jump
from app.ai_growth.matching import survey_match_result
from app.ai_growth.models import UserActivityEvent
from app.ai_growth.prediction import predict_user_for_survey, preview_summary_from_payload, survey_prediction_summary, top_respondents
from app.ai_growth.security import is_safe_internal_next, login_redirect_with_next

# Ensure additive tables exist even though api/main.py calls create_all before this
# router is included in some deployments.
Base.metadata.create_all(bind=engine)

router = APIRouter(tags=["ai-growth"])


def get_optional_current_user(request: Request, db: Session) -> Optional[User]:
    user_id = request.cookies.get("user_id")
    if not user_id:
        return None
    try:
        return db.query(User).filter(User.id == int(user_id)).first()
    except Exception:
        return None


def require_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_optional_current_user(request, db)
    if not user:
        raise HTTPException(401, "Not logged in")
    return user


def require_publisher_for_survey(db: Session, current_user: User, survey_id: int) -> Survey:
    survey = db.query(Survey).filter(Survey.id == survey_id, Survey.publisher_id == current_user.id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    return survey


@router.get("/surveys/{survey_id}/jump")
def jump_gateway(
    survey_id: int,
    request: Request,
    source: str = Query("dashboard"),
    db: Session = Depends(get_db),
):
    current_user = get_optional_current_user(request, db)
    if not current_user:
        return RedirectResponse(login_redirect_with_next(str(request.url.path) + (f"?source={source}" if source else "")), status_code=303)
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    data = start_jump(db, survey, current_user, request, source=source)
    return RedirectResponse(data["redirect_url"], status_code=303)


@router.post("/api/surveys/{survey_id}/jump/start")
def start_jump_api(
    survey_id: int,
    request: Request,
    source: str = Query("dashboard"),
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    data = start_jump(db, survey, current_user, request, source=source)
    return JSONResponse(data)


@router.get("/surveys/{survey_id}/return", response_class=HTMLResponse)
def return_from_external_task(
    survey_id: int,
    request: Request,
    token: str = Query(...),
    status: str = Query("returned"),
    db: Session = Depends(get_db),
):
    event = mark_returned(db, token, survey_id, status=status)
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    title = escape(survey.title if survey else "task")
    safe_token = escape(token, quote=True)
    # Intentionally simple HTML so no additional template dependency is required.
    return HTMLResponse(f"""
    <!doctype html>
    <html><head><meta charset=\"utf-8\"><title>Confirm completion</title>
    <style>
    body{{font-family:Inter,Arial,sans-serif;background:#faf9f5;color:#2b2118;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
    .card{{max-width:560px;background:#fff;border:1px solid #eadfce;border-radius:24px;padding:34px;box-shadow:0 18px 60px rgba(31,24,16,.12)}}
    h1{{margin:0 0 12px;font-size:28px}}p{{line-height:1.6;color:#685a4b}}button,a{{display:inline-block;margin-top:14px;border:0;border-radius:999px;padding:12px 20px;font-weight:700;text-decoration:none;cursor:pointer}}
    button{{background:#9b6a3d;color:white}}a{{color:#685a4b}}
    </style></head><body><div class=\"card\">
    <h1>Almost done</h1>
    <p>You returned from <b>{title}</b>. Click the button below to confirm that you completed the external task. The publisher will still review the response before payout.</p>
    <form method=\"post\" action=\"/surveys/{survey_id}/complete-with-token?token={safe_token}\"><button type=\"submit\">I completed this task</button></form>
    <a href=\"/dashboard\">Back to dashboard</a>
    </div></body></html>
    """)


@router.post("/surveys/{survey_id}/return")
def return_from_external_task_post(
    survey_id: int,
    token: str = Query(...),
    status: str = Query("returned"),
    db: Session = Depends(get_db),
):
    event = mark_returned(db, token, survey_id, status=status)
    return JSONResponse({"ok": True, "event_id": event.id, "status": event.status})


@router.post("/surveys/{survey_id}/complete-with-token")
def complete_with_token(
    survey_id: int,
    request: Request,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    response = complete_response_with_token(db, token, survey_id, request=request)
    return RedirectResponse("/dashboard?completed=1", status_code=303)


@router.get("/api/surveys/{survey_id}/prediction/me")
def prediction_for_me(
    survey_id: int,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    result = predict_user_for_survey(db, survey, current_user)
    match = survey_match_result(survey, current_user, strict=True)
    result["eligible"] = match.eligible
    result["matched_fields"] = match.matched_fields
    result["missing_fields"] = match.missing_fields
    return JSONResponse(result)


@router.get("/api/surveys/{survey_id}/prediction/summary")
def prediction_summary(
    survey_id: int,
    force: bool = Query(False),
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
):
    survey = require_publisher_for_survey(db, current_user, survey_id)
    return JSONResponse(survey_prediction_summary(db, survey, force=force))


@router.get("/api/surveys/{survey_id}/prediction/respondents")
def prediction_respondents(
    survey_id: int,
    limit: int = Query(20, ge=1, le=100),
    force: bool = Query(False),
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
):
    survey = require_publisher_for_survey(db, current_user, survey_id)
    return JSONResponse({"survey_id": survey.id, "respondents": top_respondents(db, survey, limit=limit, force=force)})


@router.post("/api/prediction/recompute")
async def recompute_prediction(
    request: Request,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
):
    body = await request.json()
    try:
        survey_id = int(body.get("survey_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid survey_id")
    survey = require_publisher_for_survey(db, current_user, survey_id)
    summary = survey_prediction_summary(db, survey, force=True)
    return JSONResponse(summary)


@router.post("/api/prediction/preview")
async def prediction_preview(
    request: Request,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
):
    payload = await request.json()
    payload["publisher_id"] = current_user.id
    return JSONResponse(preview_summary_from_payload(db, payload))


@router.post("/api/activity/impression")
async def record_impression(
    request: Request,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db),
):
    body = await request.json()
    try:
        survey_id = int(body.get("survey_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid survey_id")
    db.add(UserActivityEvent(
        user_id=current_user.id,
        survey_id=survey_id or None,
        event_type="task_impression",
        metadata_json={"source": body.get("source", "dashboard")},
    ))
    db.commit()
    return JSONResponse({"ok": True})
