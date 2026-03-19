from fastapi import FastAPI, APIRouter, Request, Form, Depends, HTTPException, Cookie, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from sqlalchemy import func, case
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta, timezone
import shutil
import uuid
import os

from app.database import engine, get_db
from app.models import Base, User, Survey, Response


app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
Base.metadata.create_all(bind=engine)

router = APIRouter()


# ---------------------------
# Matching helpers
# ---------------------------

EDUCATION_RANK = {
    "High School": 1,
    "Undergraduate": 2,
    "Graduate": 3,
    "PhD": 4,
}

def _education_rank(level: Optional[str], fallback: int) -> int:
    if not level:
        return fallback
    return EDUCATION_RANK.get(level, fallback)

def _is_empty(val: Optional[str]) -> bool:
    return val is None or val.strip() == "" or val.strip().lower() == "all"

def _field_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    """单值字段：target 为空则不限，否则大小写不敏感匹配。"""
    if _is_empty(target):
        return True
    if not user_val:
        return False
    return target.strip().lower() == user_val.strip().lower()

def _language_matches(target: Optional[str], user_languages: Optional[str]) -> bool:
    """Language：用户可多选，检查 target 是否在用户语言列表里。"""
    if _is_empty(target):
        return True
    if not user_languages:
        return False
    user_list = [lang.strip().lower() for lang in user_languages.split(",") if lang.strip()]
    return target.strip().lower() in user_list

def _tags_match(target_tags: Optional[str], user_tags: Optional[str]) -> bool:
    """
    Experience tags：逗号分隔多选。
    target 为空 → 不限。
    target 有值 → 用户包含其中任意一个 tag 即匹配。
    """
    if _is_empty(target_tags):
        return True
    if not user_tags:
        return False
    target_set = {t.strip().lower() for t in target_tags.split(",") if t.strip()}
    user_set = {t.strip().lower() for t in user_tags.split(",") if t.strip()}
    return bool(target_set & user_set)

def _participation_format_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    """
    参与形式：
    - target 为空 / both → 不限
    - user 填了 both → 可参加任何形式
    - 否则严格匹配
    """
    if _is_empty(target) or (target and target.strip().lower() == "both"):
        return True
    if not user_val:
        return False
    if user_val.strip().lower() == "both":
        return True
    return target.strip().lower() == user_val.strip().lower()

def _device_matches(target: Optional[str], user_val: Optional[str]) -> bool:
    """
    设备要求：
    - target 为空 / any → 不限
    - user 填了 any → 什么设备都有
    - 否则严格匹配
    """
    if _is_empty(target) or (target and target.strip().lower() == "any"):
        return True
    if not user_val:
        return False
    if user_val.strip().lower() == "any":
        return True
    return target.strip().lower() == user_val.strip().lower()


# ---------------------------
# Other helpers
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

def _clean_target(val: Optional[str]) -> str:
    """把 None / 'all' 统一存为空字符串。"""
    return '' if not val or val.strip().lower() == 'all' else val


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
# Register
# ---------------------------

@app.get("/register", response_class=HTMLResponse)
def show_register(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": None}
    )

@app.post("/register", response_class=HTMLResponse)
async def do_register(
    request: Request,
    db: Session = Depends(get_db)
):
    form = await request.form()
    email = form.get("email") or ""
    password = form.get("password") or ""
    confirm = form.get("confirm") or ""

    if password != confirm:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Passwords do not match"})
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already exists"})

    language_list = form.getlist("language")
    experience_list = form.getlist("experience_tags")

    user = User(
        email=email,
        password=pwd_context.hash(password),
        age_range=form.get("age_range"),
        education_level=form.get("education_level"),
        field=form.get("field"),
        status=form.get("status"),
        state=form.get("state"),
        ethnicity=form.get("ethnicity"),
        mental_health_diagnosis=form.get("mental_health_diagnosis"),
        physical_health_diagnosis=form.get("physical_health_diagnosis"),
        sexual_orientation=form.get("sexual_orientation"),
        sport_type=form.get("sport_type"),
        sport_frequency=form.get("sport_frequency"),
        smoking=form.get("smoking"),
        cannabis_use=form.get("cannabis_use"),
        language=",".join(language_list) if language_list else None,
        student_status=form.get("student_status"),
        year_in_school=form.get("year_in_school"),
        international_domestic=form.get("international_domestic"),
        experience_tags=",".join(experience_list) if experience_list else None,
        participation_format=form.get("participation_format"),
        device_type=form.get("device_type"),
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    response = RedirectResponse("/dashboard?welcome=1", status_code=303)
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
# Dashboard (participant view)
# ---------------------------

URGENCY_RANK = {
    "within_3_days": 3,
    "within_1_week": 2,
    "flexible": 1,
}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    all_published = db.query(Survey).filter(Survey.status == "published").all()

    user_edu_min = _education_rank(current_user.education_level, fallback=0)
    user_edu_max = _education_rank(current_user.education_level, fallback=999)

    def survey_matches(s: Survey) -> bool:
        # 原有字段
        if not _field_matches(s.target_age_range, current_user.age_range):
            return False
        if s.target_education_min is not None:
            if user_edu_min < s.target_education_min:
                return False
        if s.target_education_max is not None:
            if user_edu_max > s.target_education_max:
                return False
        if not _field_matches(s.target_field, current_user.field):
            return False
        if not _field_matches(s.target_status, current_user.status):
            return False
        if not _field_matches(s.target_state, current_user.state):
            return False
        if not _language_matches(s.target_language, current_user.language):
            return False
        if not _field_matches(s.target_ethnicity, current_user.ethnicity):
            return False
        if not _field_matches(s.target_sexual_orientation, current_user.sexual_orientation):
            return False
        if not _field_matches(s.target_mental_health_diagnosis, current_user.mental_health_diagnosis):
            return False
        if not _field_matches(s.target_physical_health_diagnosis, current_user.physical_health_diagnosis):
            return False
        if not _field_matches(s.target_sport_type, current_user.sport_type):
            return False
        if not _field_matches(s.target_sport_frequency, current_user.sport_frequency):
            return False
        if not _field_matches(s.target_smoking, current_user.smoking):
            return False
        if not _field_matches(s.target_cannabis_use, current_user.cannabis_use):
            return False
        # 新增字段
        if not _field_matches(getattr(s, 'target_student_status', None), getattr(current_user, 'student_status', None)):
            return False
        if not _field_matches(getattr(s, 'target_year_in_school', None), getattr(current_user, 'year_in_school', None)):
            return False
        if not _field_matches(getattr(s, 'target_international_domestic', None), getattr(current_user, 'international_domestic', None)):
            return False
        if not _tags_match(getattr(s, 'target_experience_tags', None), getattr(current_user, 'experience_tags', None)):
            return False
        if not _participation_format_matches(getattr(s, 'target_participation_format', None), getattr(current_user, 'participation_format', None)):
            return False
        if not _device_matches(getattr(s, 'target_device', None), getattr(current_user, 'device_type', None)):
            return False
        return True

    matched = [s for s in all_published if survey_matches(s)]

    # urgency 高的排前面，其次按 published_at 降序
    matched.sort(key=lambda s: (
        -URGENCY_RANK.get(getattr(s, 'urgency_level', None) or 'flexible', 1),
        -(s.published_at.timestamp() if s.published_at else 0)
    ))

    surveys_data = []
    for s in matched:
        started_cnt = db.query(Response).filter(Response.survey_id == s.id).count()
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
            "is_completed": is_completed,
            "urgency": getattr(s, 'urgency_level', None) or 'flexible',
            "incentive_type": getattr(s, 'incentive_type', None),
        })

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


# ---------------------------
# Dashboard stats API
# ---------------------------

@app.get("/api/dashboard/stats")
def get_dashboard_stats(
    timezone_offset: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
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


# ---------------------------
# Survey start / complete / modify
# ---------------------------

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

    db.commit()
    return RedirectResponse(url="/dashboard", status_code=302)


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
# Publish page
# ---------------------------

@app.get("/publish", response_class=HTMLResponse)
def publish_page(request: Request):
    return templates.TemplateResponse("publish.html", {"request": request})


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
    urgency_level: str = Form(None),
    incentive_type: str = Form(None),
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
    target_student_status: str = Form(None),
    target_year_in_school: str = Form(None),
    target_international_domestic: str = Form(None),
    target_participation_format: str = Form(None),
    target_device: str = Form(None),
    cover_image: UploadFile = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    form = await request.form()
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))
    experience_list = form.getlist("target_experience_tags")
    target_experience_tags = ",".join(experience_list) if experience_list else None

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
        task_type=task_type,
        category=category,
        estimated_time=estimated_time,
        reward_amount=reward_amount,
        target_responses=target_responses,
        urgency_level=_clean_target(urgency_level),
        incentive_type=_clean_target(incentive_type),
        target_age_range=_clean_target(target_age_range),
        target_education_min=target_education_min,
        target_education_max=target_education_max,
        target_field=_clean_target(target_field),
        target_status=_clean_target(target_status),
        target_state=_clean_target(target_state),
        target_language=_clean_target(target_language),
        target_ethnicity=_clean_target(target_ethnicity),
        target_sexual_orientation=_clean_target(target_sexual_orientation),
        target_mental_health_diagnosis=_clean_target(target_mental_health_diagnosis),
        target_physical_health_diagnosis=_clean_target(target_physical_health_diagnosis),
        target_sport_type=_clean_target(target_sport_type),
        target_sport_frequency=_clean_target(target_sport_frequency),
        target_smoking=_clean_target(target_smoking),
        target_cannabis_use=_clean_target(target_cannabis_use),
        target_student_status=_clean_target(target_student_status),
        target_year_in_school=_clean_target(target_year_in_school),
        target_international_domestic=_clean_target(target_international_domestic),
        target_experience_tags=target_experience_tags,
        target_participation_format=_clean_target(target_participation_format),
        target_device=_clean_target(target_device),
        image_url=image_url,
        status="draft",
        published_at=None,
        closed_at=None,
    )
    db.add(survey)
    db.commit()
    return RedirectResponse("/publisher", status_code=303)


# ---------------------------
# Survey status management
# ---------------------------

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


# ---------------------------
# Edit survey
# ---------------------------

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
    urgency_level: str = Form(None),
    incentive_type: str = Form(None),
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
    target_student_status: str = Form(None),
    target_year_in_school: str = Form(None),
    target_international_domestic: str = Form(None),
    target_participation_format: str = Form(None),
    target_device: str = Form(None),
    cover_image: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    form = await request.form()
    target_education_min = _parse_optional_int(form.get("target_education_min"))
    target_education_max = _parse_optional_int(form.get("target_education_max"))
    experience_list = form.getlist("target_experience_tags")
    target_experience_tags = ",".join(experience_list) if experience_list else None

    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if survey:
        current_responses = db.query(Response).filter(
            Response.survey_id == survey_id,
            Response.status == "completed"
        ).count()

        survey.title = title
        survey.description = description
        survey.form_url = form_url
        survey.task_type = task_type
        survey.category = category
        survey.estimated_time = estimated_time
        survey.reward_amount = reward_amount
        survey.target_responses = current_responses + additional_needed
        survey.urgency_level = _clean_target(urgency_level)
        survey.incentive_type = _clean_target(incentive_type)
        survey.target_age_range = _clean_target(target_age_range)
        survey.target_education_min = target_education_min
        survey.target_education_max = target_education_max
        survey.target_field = _clean_target(target_field)
        survey.target_status = _clean_target(target_status)
        survey.target_state = _clean_target(target_state)
        survey.target_language = _clean_target(target_language)
        survey.target_ethnicity = _clean_target(target_ethnicity)
        survey.target_sexual_orientation = _clean_target(target_sexual_orientation)
        survey.target_mental_health_diagnosis = _clean_target(target_mental_health_diagnosis)
        survey.target_physical_health_diagnosis = _clean_target(target_physical_health_diagnosis)
        survey.target_sport_type = _clean_target(target_sport_type)
        survey.target_sport_frequency = _clean_target(target_sport_frequency)
        survey.target_smoking = _clean_target(target_smoking)
        survey.target_cannabis_use = _clean_target(target_cannabis_use)
        survey.target_student_status = _clean_target(target_student_status)
        survey.target_year_in_school = _clean_target(target_year_in_school)
        survey.target_international_domestic = _clean_target(target_international_domestic)
        survey.target_experience_tags = target_experience_tags
        survey.target_participation_format = _clean_target(target_participation_format)
        survey.target_device = _clean_target(target_device)

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


# ---------------------------
# Profile
# ---------------------------

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
    language_list = form.getlist("language")
    experience_list = form.getlist("experience_tags")

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
    current_user.language = ",".join(language_list) if language_list else None
    current_user.student_status = form.get("student_status")
    current_user.year_in_school = form.get("year_in_school")
    current_user.international_domestic = form.get("international_domestic")
    current_user.experience_tags = ",".join(experience_list) if experience_list else None
    current_user.participation_format = form.get("participation_format")
    current_user.device_type = form.get("device_type")

    db.commit()
    return RedirectResponse("/choice", status_code=303)


# ---------------------------
# AI Fill
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