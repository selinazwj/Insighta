from fastapi import FastAPI, APIRouter, Request, Form, Depends, HTTPException, Cookie, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from sqlalchemy import or_, func, case
from pathlib import Path
from typing import Optional
import shutil
import uuid
import os

from app.database import engine, get_db
from app.models import Base, User, Survey, Response, Notification


app = FastAPI()

# Template path
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory="app/templates")

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
Base.metadata.create_all(bind=engine)


router = APIRouter()
# ---------------------------
# Index
# ---------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "show": None, "error": None}
    )

# ---------------------------
# Login
# ---------------------------
from fastapi import Request

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        user = db.query(User).filter(User.email == email).first()
    except Exception as e:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Database error: {e}"}
        )

    if not user or not pwd_context.verify(password, user.password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password"}
        )

    response = RedirectResponse("/choice", status_code=303)
    response.set_cookie("user_id", str(user.id), httponly=True)
    return response

# ---------------------------
# Register (GET)
# ---------------------------
@app.get("/register", response_class=HTMLResponse)
def show_register(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": None}
    )

# ---------------------------
# Register (POST)
# ---------------------------
@app.post("/register", response_class=HTMLResponse)
async def do_register(
    request: Request,
    db: Session = Depends(get_db)
):
    form = await request.form()
    email = form.get("email") or ""
    password = form.get("password") or ""
    confirm = form.get("confirm") or ""
    age_range = form.get("age_range")
    education_level = form.get("education_level")
    field = form.get("field")
    status = form.get("status")
    state = form.get("state")
    ethnicity = form.get("ethnicity")
    mental_health_diagnosis = form.get("mental_health_diagnosis")
    physical_health_diagnosis = form.get("physical_health_diagnosis")
    sexual_orientation = form.get("sexual_orientation")
    sport_type = form.get("sport_type")
    sport_frequency = form.get("sport_frequency")
    smoking = form.get("smoking")
    cannabis_use = form.get("cannabis_use")
    language_list = form.getlist("language")
    language = ",".join(language_list) if language_list else None

    if password != confirm:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Passwords do not match"})

    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already exists"})

    hashed_password = pwd_context.hash(password)

    user = User(
        email=email,
        password=hashed_password,
        age_range=age_range,
        education_level=education_level,
        field=field,
        status=status,
        state=state,
        ethnicity=ethnicity,
        mental_health_diagnosis=mental_health_diagnosis,
        physical_health_diagnosis=physical_health_diagnosis,
        sexual_orientation=sexual_orientation,
        sport_type=sport_type,
        sport_frequency=sport_frequency,
        smoking=smoking,
        cannabis_use=cannabis_use,
        language=language
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse("/choice", status_code=303)
    response.set_cookie("user_id", str(user.id))
    return response

# ---------------------------
# Current user
# ---------------------------
def get_current_user(
    user_id: str = Cookie(None),
    db: Session = Depends(get_db)
):
    if not user_id:
        raise HTTPException(401, "Not logged in")

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(401, "User not found")
    return user

# ---------------------------
# Choice page
# ---------------------------
@app.get("/choice", response_class=HTMLResponse)
def choice(request: Request, user_id: str = Cookie(None), db: Session = Depends(get_db)):
    current_user = None
    if user_id:
        try:
            current_user = db.query(User).filter(User.id == int(user_id)).first()
        except:
            pass
    return templates.TemplateResponse("choice.html", {"request": request, "current_user": current_user})

# ---------------------------
# Publisher Dashboard
# ---------------------------
@app.get("/publisher", response_class=HTMLResponse)
def publisher_dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    all_items = db.query(Survey).filter(Survey.publisher_id == current_user.id).all()
    survey_ids = [s.id for s in all_items]

    if not survey_ids:
        completed_map = {}
    else:
        completed_map = dict(
            db.query(
                Response.survey_id,
                func.sum(case((Response.status == "completed", 1), else_=0)).label("completed_cnt"),
            )
            .filter(Response.survey_id.in_(survey_ids))
            .group_by(Response.survey_id)
            .all()
        )

    survey_items = [s for s in all_items if _normalize_task_type(getattr(s, "task_type", None)) == "survey"]
    interview_items = [s for s in all_items if _normalize_task_type(getattr(s, "task_type", None)) == "interview"]

    return templates.TemplateResponse(
        "publisher.html",
        {
            "request": request,
            "surveys": survey_items,
            "interviews": interview_items,
            "completed_map": completed_map,
            "current_user": current_user
        }
    )

# ---------------------------
# Delete survey
# ---------------------------
@app.post("/publisher/delete/{survey_id}")
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

    db.delete(survey)
    db.commit()
    return RedirectResponse("/publisher", status_code=303)

# ---------------------------
# Dashboard (filler view)
# ---------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    surveys = db.query(Survey).filter(
        Survey.status == "published",
        or_(Survey.target_age_range == None, Survey.target_age_range == '', Survey.target_age_range == current_user.age_range),
        or_(
            Survey.target_education_min == None,
            Survey.target_education_min <= case(
                (current_user.education_level == "High School", 1),
                (current_user.education_level == "Undergraduate", 2),
                (current_user.education_level == "Graduate", 3),
                (current_user.education_level == "PhD", 4),
                else_=0
            )
        ),
        or_(
            Survey.target_education_max == None,
            Survey.target_education_max >= case(
                (current_user.education_level == "High School", 1),
                (current_user.education_level == "Undergraduate", 2),
                (current_user.education_level == "Graduate", 3),
                (current_user.education_level == "PhD", 4),
                else_=999
            )
        ),
        or_(Survey.target_field == None, Survey.target_field == '', Survey.target_field == current_user.field),
        or_(Survey.target_status == None, Survey.target_status == '', Survey.target_status == current_user.status),
        or_(Survey.target_state == None, Survey.target_state == '', Survey.target_state == current_user.state),
        or_(
            Survey.target_language == None,
            Survey.target_language == '',
            Survey.target_language == current_user.language,
            Survey.target_language.in_([x.strip() for x in (current_user.language or "").split(",") if x.strip()])
        ),
        or_(Survey.target_ethnicity == None, Survey.target_ethnicity == '', Survey.target_ethnicity == current_user.ethnicity),
        or_(Survey.target_sexual_orientation == None, Survey.target_sexual_orientation == '', Survey.target_sexual_orientation == current_user.sexual_orientation),
        or_(Survey.target_mental_health_diagnosis == None, Survey.target_mental_health_diagnosis == '', Survey.target_mental_health_diagnosis == current_user.mental_health_diagnosis),
        or_(Survey.target_physical_health_diagnosis == None, Survey.target_physical_health_diagnosis == '', Survey.target_physical_health_diagnosis == current_user.physical_health_diagnosis),
        or_(Survey.target_sport_type == None, Survey.target_sport_type == '', Survey.target_sport_type == current_user.sport_type),
        or_(Survey.target_sport_frequency == None, Survey.target_sport_frequency == '', Survey.target_sport_frequency == current_user.sport_frequency),
        or_(Survey.target_smoking == None, Survey.target_smoking == '', Survey.target_smoking == current_user.smoking),
        or_(Survey.target_cannabis_use == None, Survey.target_cannabis_use == '', Survey.target_cannabis_use == current_user.cannabis_use),
    ).all()

    surveys_data = []
    for s in surveys:
        started_cnt = db.query(Response).filter(
            Response.survey_id == s.id
        ).count()

        completed_cnt = db.query(Response).filter(
            Response.survey_id == s.id,
            Response.status == "completed"
        ).count()

        user_response = db.query(Response).filter(
            Response.survey_id == s.id,
            Response.participant_id == current_user.id
        ).first()

        is_completed = user_response and user_response.status == "completed"

        category_images = {
            "research": "/static/psych.jpg",
            "life": "/static/campus_life.jpg",
            "clubs": "/static/fb.jpg",
            "market": "/static/habit.png",
            "academic": "/static/r2.jpg",
            "other": "/static/food.jpeg"
        }

        surveys_data.append({
            "id": s.id,
            "title": s.title,
            "desc": s.description,
            "link": s.form_url,
            "type": _normalize_task_type(getattr(s, "task_type", None)),
            "category": s.category,
            "time": f"{s.estimated_time} min",
            "reward": f"${s.reward_amount}",
            "responses": f"{completed_cnt}/{s.target_responses}",
            "started": started_cnt,
            "img": s.image_url if s.image_url else category_images.get(s.category, "/static/psych.jpg"),
            "is_completed": is_completed
        })

    from datetime import datetime, timedelta, timezone

    if timezone_offset is not None:
        user_tz = timezone(timedelta(minutes=-timezone_offset))
        now_user = datetime.now(user_tz)
        today_start_user = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_user.astimezone(timezone.utc)
    else:
        now_utc = datetime.now(timezone.utc)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    completed_today = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= today_start_utc
    ).count()

    completed_responses = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None)
    ).all()

    total_earned = 0.0
    for resp in completed_responses:
        survey = db.query(Survey).filter(Survey.id == resp.survey_id).first()
        if survey:
            total_earned += survey.reward_amount

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "surveys": surveys_data,
            "completed_today": completed_today,
            "total_earned": total_earned,
            "available_surveys": len(surveys_data),
            "current_user": current_user
        }
    )

@app.get("/api/dashboard/stats")
def get_dashboard_stats(
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from datetime import datetime, timedelta, timezone

    if timezone_offset is not None:
        user_tz = timezone(timedelta(minutes=-timezone_offset))
        now_user = datetime.now(user_tz)
        today_start_user = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_user.astimezone(timezone.utc)
    else:
        now_utc = datetime.now(timezone.utc)
        today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    completed_today = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None),
        Response.completed_at >= today_start_utc
    ).count()

    completed_responses = db.query(Response).filter(
        Response.participant_id == current_user.id,
        Response.status == "completed",
        Response.completed_at.isnot(None)
    ).all()

    total_earned = 0.0
    for resp in completed_responses:
        survey = db.query(Survey).filter(Survey.id == resp.survey_id).first()
        if survey:
            total_earned += survey.reward_amount

    return JSONResponse({
        "completed_today": completed_today,
        "total_earned": total_earned
    })


@app.post("/surveys/{survey_id}/start")
def start_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    if survey.status != "published":
        raise HTTPException(400, "Survey not published")

    existing = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.participant_id == current_user.id
    ).first()

    if not existing:
        db.add(Response(survey_id=survey_id, participant_id=current_user.id, status="started"))
        db.commit()

    return {"message": "Survey started successfully"}

@app.post("/surveys/{survey_id}/complete")
def complete_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from datetime import datetime, timezone
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(404, "Survey not found")
    if survey.status != "published":
        raise HTTPException(400, "Survey not published")

    r = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.participant_id == current_user.id
    ).first()

    if not r:
        r = Response(survey_id=survey_id, participant_id=current_user.id, status="started")
        db.add(r)

    if r.status != "completed":
        r.status = "completed"
        r.completed_at = datetime.now(timezone.utc)

        # Create notification for the publisher
        notif = Notification(
            publisher_id=survey.publisher_id,
            participant_id=current_user.id,
            survey_id=survey_id,
            participant_email=current_user.email,
            survey_title=survey.title,
            task_type=getattr(survey, "task_type", "survey") or "survey",
            status="pending"
        )
        db.add(notif)

    db.commit()
    return JSONResponse({"ok": True})

@app.post("/surveys/{survey_id}/modify")
def modify_completed_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    r = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.participant_id == current_user.id
    ).first()

    if r and r.status == "completed":
        r.status = "started"
        r.completed_at = None
        db.commit()
        return {"message": "Response modified"}

    return JSONResponse({"detail": "Response not found or not completed"}, status_code=404)


# ---------------------------
# Notifications API
# ---------------------------
@app.get("/api/notifications")
def get_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    notifs = db.query(Notification).filter(
        Notification.publisher_id == current_user.id
    ).order_by(Notification.created_at.desc()).all()

    return JSONResponse([{
        "id": n.id,
        "participant_email": n.participant_email,
        "survey_title": n.survey_title,
        "task_type": n.task_type or "survey",
        "status": n.status,
        "created_at": n.created_at.strftime("%b %d, %H:%M") if n.created_at else ""
    } for n in notifs])


@app.post("/api/notifications/{notif_id}/accept")
def accept_notification(
    notif_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    n = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.publisher_id == current_user.id
    ).first()
    if not n:
        raise HTTPException(404, "Notification not found")
    n.status = "accepted"
    db.commit()
    return {"message": "accepted"}


@app.post("/api/notifications/{notif_id}/reject")
def reject_notification(
    notif_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    n = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.publisher_id == current_user.id
    ).first()
    if not n:
        raise HTTPException(404, "Notification not found")
    n.status = "rejected"
    db.commit()
    return {"message": "rejected"}


# ---------------------------
# Publish page
# ---------------------------
@app.get("/publish", response_class=HTMLResponse)
def publish_page(request: Request):
    return templates.TemplateResponse("publish.html", {"request": request})

# ---------------------------
# Helper functions
# ---------------------------
def _parse_optional_int(v) -> Optional[int]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _normalize_task_type(value: Optional[str]) -> str:
    return "interview" if value == "interview" else "survey"


# ---------------------------
# Publish survey
# ---------------------------
@app.post("/publish")
async def publish_survey(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    form_url: str = Form(...),
    task_type: str = Form("survey"),
    category: str = Form(...),
    estimated_time: int = Form(...),
    reward_amount: float = Form(...),
    target_responses: int = Form(...),
    target_age_range: str = Form(None),
    target_field: str = Form(None),
    target_status: str = Form(None),
    target_state: str = Form(None),
    target_language: str = Form(None),
    target_ethnicity: str = Form(None),
    target_sexual_orientation: str = Form(None),
    target_mental_health_diagnosis: str = Form(None),
    target_physical_health_diagnosis: str = Form(None),
    target_sport_type: str = Form(None),
    target_sport_frequency: str = Form(None),
    target_smoking: str = Form(None),
    target_cannabis_use: str = Form(None),
    cover_image: UploadFile = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    form = await request.form()
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))

    image_url = None
    if cover_image and cover_image.filename:
        uploads_dir = Path("app/static/uploads")
        uploads_dir.mkdir(exist_ok=True)
        file_extension = Path(cover_image.filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = uploads_dir / unique_filename
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(cover_image.file, buffer)
        image_url = f"/static/uploads/{unique_filename}"

    survey = Survey(
        publisher_id=current_user.id,
        title=title,
        description=description,
        form_url=form_url,
        task_type=_normalize_task_type(task_type),
        category=category,
        estimated_time=estimated_time,
        reward_amount=reward_amount,
        target_responses=target_responses,
        target_age_range='' if not target_age_range or target_age_range == 'all' else target_age_range,
        target_education_min=target_education_min,
        target_education_max=target_education_max,
        target_field='' if not target_field or target_field == 'all' else target_field,
        target_status='' if not target_status or target_status == 'all' else target_status,
        target_state='' if not target_state or target_state == 'all' else target_state,
        target_language='' if not target_language or target_language == 'all' else target_language,
        target_ethnicity='' if not target_ethnicity or target_ethnicity == 'all' else target_ethnicity,
        target_sexual_orientation='' if not target_sexual_orientation or target_sexual_orientation == 'all' else target_sexual_orientation,
        target_mental_health_diagnosis='' if not target_mental_health_diagnosis or target_mental_health_diagnosis == 'all' else target_mental_health_diagnosis,
        target_physical_health_diagnosis='' if not target_physical_health_diagnosis or target_physical_health_diagnosis == 'all' else target_physical_health_diagnosis,
        target_sport_type='' if not target_sport_type or target_sport_type == 'all' else target_sport_type,
        target_sport_frequency='' if not target_sport_frequency or target_sport_frequency == 'all' else target_sport_frequency,
        target_smoking='' if not target_smoking or target_smoking == 'all' else target_smoking,
        target_cannabis_use='' if not target_cannabis_use or target_cannabis_use == 'all' else target_cannabis_use,
        image_url=image_url,
        status="draft",
        published_at=None,
        closed_at=None,
    )
    db.add(survey)
    db.commit()
    return RedirectResponse("/publisher", status_code=303)

from datetime import datetime

@app.post("/surveys/{survey_id}/publish")
def publish_existing_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    s = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not s:
        raise HTTPException(404, "Survey not found")
    if s.status != "published":
        s.status = "published"
        s.published_at = datetime.utcnow()
        s.closed_at = None
    db.commit()
    return RedirectResponse("/publisher", status_code=303)


@app.post("/surveys/{survey_id}/close")
def close_existing_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    s = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not s:
        raise HTTPException(404, "Survey not found")
    if s.status != "closed":
        s.status = "closed"
        s.closed_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/publisher", status_code=303)

@app.post("/surveys/{survey_id}/reopen")
def reopen_closed_survey(
    survey_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    s = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id
    ).first()
    if not s:
        raise HTTPException(404, "Survey not found")
    if s.status == "closed":
        s.status = "published"
        s.closed_at = None
    db.commit()
    return RedirectResponse("/publisher", status_code=303)

@app.get("/publisher/edit/{survey_id}")
def edit_survey_get(request: Request, survey_id: int, db: Session = Depends(get_db)):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    current_responses = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed"
    ).count()
    survey.current_responses = current_responses
    return templates.TemplateResponse("edit_publish.html", {"request": request, "survey": survey})

@app.post("/publisher/edit/{survey_id}")
async def edit_survey_post(
    request: Request,
    survey_id: int,
    title: str = Form(...),
    description: str = Form(...),
    form_url: str = Form(...),
    task_type: str = Form("survey"),
    category: str = Form(...),
    estimated_time: int = Form(...),
    reward_amount: float = Form(...),
    additional_needed: int = Form(...),
    target_age_range: str = Form(None),
    target_field: str = Form(None),
    target_status: str = Form(None),
    target_state: str = Form(None),
    target_language: str = Form(None),
    target_ethnicity: str = Form(None),
    target_sexual_orientation: str = Form(None),
    target_mental_health_diagnosis: str = Form(None),
    target_physical_health_diagnosis: str = Form(None),
    target_sport_type: str = Form(None),
    target_sport_frequency: str = Form(None),
    target_smoking: str = Form(None),
    target_cannabis_use: str = Form(None),
    cover_image: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    form = await request.form()
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))

    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if survey:
        current_responses = db.query(Response).filter(
            Response.survey_id == survey_id,
            Response.status == "completed"
        ).count()

        survey.title = title
        survey.description = description
        survey.form_url = form_url
        survey.task_type = _normalize_task_type(task_type)
        survey.category = category
        survey.estimated_time = estimated_time
        survey.reward_amount = reward_amount
        survey.target_responses = current_responses + additional_needed
        survey.target_age_range = '' if not target_age_range or target_age_range == 'all' else target_age_range
        survey.target_education_min = target_education_min
        survey.target_education_max = target_education_max
        survey.target_field = '' if not target_field or target_field == 'all' else target_field
        survey.target_status = '' if not target_status or target_status == 'all' else target_status
        survey.target_state = '' if not target_state or target_state == 'all' else target_state
        survey.target_language = '' if not target_language or target_language == 'all' else target_language
        survey.target_ethnicity = '' if not target_ethnicity or target_ethnicity == 'all' else target_ethnicity
        survey.target_sexual_orientation = '' if not target_sexual_orientation or target_sexual_orientation == 'all' else target_sexual_orientation
        survey.target_mental_health_diagnosis = '' if not target_mental_health_diagnosis or target_mental_health_diagnosis == 'all' else target_mental_health_diagnosis
        survey.target_physical_health_diagnosis = '' if not target_physical_health_diagnosis or target_physical_health_diagnosis == 'all' else target_physical_health_diagnosis
        survey.target_sport_type = '' if not target_sport_type or target_sport_type == 'all' else target_sport_type
        survey.target_sport_frequency = '' if not target_sport_frequency or target_sport_frequency == 'all' else target_sport_frequency
        survey.target_smoking = '' if not target_smoking or target_smoking == 'all' else target_smoking
        survey.target_cannabis_use = '' if not target_cannabis_use or target_cannabis_use == 'all' else target_cannabis_use

        if cover_image and cover_image.filename:
            uploads_dir = Path("app/static/uploads")
            uploads_dir.mkdir(exist_ok=True)
            file_extension = Path(cover_image.filename).suffix
            unique_filename = f"{uuid.uuid4()}{file_extension}"
            file_path = uploads_dir / unique_filename
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(cover_image.file, buffer)
            survey.image_url = f"/static/uploads/{unique_filename}"

        db.commit()
    return RedirectResponse("/publisher", status_code=303)

@app.get("/profile", response_class=HTMLResponse)
def profile_get(request: Request, current_user: User = Depends(get_current_user)):
    prev_url = request.headers.get("referer", "/choice")
    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "user": current_user, "prev_url": prev_url}
    )

@app.post("/profile")
async def profile_post(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    form = await request.form()
    current_user.username = form.get("username")
    current_user.email = form.get("email") or ""
    current_user.age_range = form.get("age_range")
    current_user.education_level = form.get("education_level")
    current_user.field = form.get("field")
    current_user.status = form.get("status")
    current_user.state = form.get("state")
    current_user.ethnicity = form.get("ethnicity")
    current_user.mental_health_diagnosis = form.get("mental_health_diagnosis")
    current_user.physical_health_diagnosis = form.get("physical_health_diagnosis")
    current_user.sexual_orientation = form.get("sexual_orientation")
    current_user.sport_type = form.get("sport_type")
    current_user.sport_frequency = form.get("sport_frequency")
    current_user.smoking = form.get("smoking")
    current_user.cannabis_use = form.get("cannabis_use")
    language_list = form.getlist("language")
    current_user.language = ",".join(language_list) if language_list else None
    db.commit()
    return RedirectResponse("/choice", status_code=303)

# ---------------------------
# AI Fill Survey
# ---------------------------
@app.post("/api/ai-fill")
async def ai_fill(request: Request, current_user: User = Depends(get_current_user)):
    try:
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            raise HTTPException(400, "Prompt is required")

        import anthropic, json, re
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": f"""You are helping a researcher fill out a survey publishing form.
Based on this description: "{prompt}"

Return ONLY a valid JSON object with these exact fields, no extra text:
{{
  "title": "clear survey title under 10 words",
  "description": "2-3 sentence description of the survey purpose",
  "category": "one of: research, life, clubs, market, academic, other",
  "estimated_time": 5,
  "reward_amount": 5,
  "target_responses": 100
}}"""
            }]
        )

        text = message.content[0].text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            raise HTTPException(500, "AI response parsing failed")

        result = json.loads(match.group())
        return JSONResponse(result)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(500, str(e))


app.include_router(router)