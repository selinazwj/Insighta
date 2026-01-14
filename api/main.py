from fastapi import FastAPI, APIRouter, Request, Form, Depends, HTTPException, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from sqlalchemy import or_, func, case
from pathlib import Path

from app.database import engine, get_db
from app.models import Base, User, Survey, Response


app = FastAPI()

# 确保模板路径正确
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory="app/templates")

# 挂载静态文件
app.mount("/static", StaticFiles(directory="app/static"), name="static")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
Base.metadata.create_all(bind=engine)


router = APIRouter()
# ---------------------------
# 首页
# ---------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "show": None, "error": None}
    )

# ---------------------------
# 登录
# ---------------------------
@app.post("/login")
def login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not pwd_context.verify(password, user.password):
        return templates.TemplateResponse(
            "index.html",
            {"request": {}, "show": "login", "error": "Invalid email or password"}
        )

    response = RedirectResponse("/choice", status_code=303)
    response.set_cookie("user_id", str(user.id))
    return response

# ---------------------------
# 注册页面
# ---------------------------
# ---------------------------
# 注册页面显示（GET）
# ---------------------------
@app.get("/register", response_class=HTMLResponse)
def show_register(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": None}
    )

# ---------------------------
# 注册处理（POST）
# ---------------------------
@app.post("/register", response_class=HTMLResponse)
def do_register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),

    age_range: str = Form(None),
    education_level: str = Form(None),
    field: str = Form(None),
    status: str = Form(None),
    country: str = Form(None),
    language: str = Form(None),

    db: Session = Depends(get_db)
):
    # 密码确认
    if password != confirm:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Passwords do not match"})

    # 邮箱是否已存在
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already exists"})

    # 加密密码
    hashed_password = pwd_context.hash(password)

    # 创建用户
    user = User(
        email=email,
        password=hashed_password,
        age_range=age_range,
        education_level=education_level,
        field=field,
        status=status,
        country=country,
        language=language
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    # 登录态
    response = RedirectResponse("/choice", status_code=303)
    response.set_cookie("user_id", str(user.id))
    return response

# ---------------------------
# 当前用户
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
# 选择页
# ---------------------------
@app.get("/choice", response_class=HTMLResponse)
def choice(request: Request):
    return templates.TemplateResponse("choice.html", {"request": request})

# ---------------------------
# Publisher Dashboard
# ---------------------------
@app.get("/publisher", response_class=HTMLResponse)
def publisher_dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    surveys = db.query(Survey).filter(Survey.publisher_id == current_user.id).all()
    survey_ids = [s.id for s in surveys]

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

    return templates.TemplateResponse(
        "publisher.html",
        {"request": request, "surveys": surveys, "completed_map": completed_map}
    )



# ---------------------------
# 删除 survey
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
# Dashboard（填写者视角）
# ---------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    surveys = db.query(Survey).filter(
        or_(Survey.target_age_range == None, Survey.target_age_range == current_user.age_range),
        or_(Survey.target_education == None, Survey.target_education == current_user.education_level),
        or_(Survey.target_field == None, Survey.target_field == current_user.field),
        or_(Survey.target_status == None, Survey.target_status == current_user.status),
        or_(Survey.target_country == None, Survey.target_country == current_user.country),
        or_(Survey.target_language == None, Survey.target_language == current_user.language)
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

        surveys_data.append({
            "id": s.id,
            "title": s.title,
            "desc": s.description,
            "link": s.form_url,
            "category": s.category,
            "time": f"{s.estimated_time} min",
            "reward": f"${s.reward_amount}",
            "responses": f"{completed_cnt}/{s.target_responses}",
            "started": started_cnt,
            "img": "/static/default.jpg"
        })

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "surveys": surveys_data}
    )


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

    # 防重复：同一用户同一 survey 只创建一条 started
    existing = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.participant_id == current_user.id
    ).first()

    if not existing:
        db.add(Response(survey_id=survey_id, participant_id=current_user.id, status="started"))
        db.commit()

    return RedirectResponse(url=survey.form_url, status_code=302)

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
        r.completed_at = datetime.utcnow()

    db.commit()
    return RedirectResponse(url="/dashboard", status_code=302)


# ---------------------------
# 发布页面
# ---------------------------
@app.get("/publish", response_class=HTMLResponse)
def publish_page(request: Request):
    return templates.TemplateResponse("publish.html", {"request": request})

# ---------------------------
# 发布 survey
# ---------------------------
@app.post("/publish")
def publish_survey(
    title: str = Form(...),
    description: str = Form(...),
    form_url: str = Form(...),
    category: str = Form(...),
    estimated_time: int = Form(...),
    reward_amount: float = Form(...),
    target_responses: int = Form(...),
    target_age_range: str = Form(None),
    target_education: str = Form(None),
    target_country: str = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    survey = Survey(
    publisher_id=current_user.id,
    title=title,
    description=description,
    form_url=form_url,
    category=category,
    estimated_time=estimated_time,
    reward_amount=reward_amount,
    target_responses=target_responses,
    target_age_range=target_age_range,
    target_education=target_education,
    target_country=target_country,
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

@app.get("/publisher/edit/{survey_id}")
def edit_survey_get(request: Request, survey_id: int, db: Session = Depends(get_db)):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    return templates.TemplateResponse("edit_publish.html", {"request": request, "survey": survey})

@app.post("/publisher/edit/{survey_id}")
def edit_survey_post(
    request: Request,
    survey_id: int,
    title: str = Form(...),
    description: str = Form(...),
    form_url: str = Form(...),
    category: str = Form(...),
    estimated_time: int = Form(...),
    reward_amount: float = Form(...),
    target_responses: int = Form(...),
    target_age_range: str = Form(None),
    target_education: str = Form(None),
    target_field: str = Form(None),
    target_status: str = Form(None),
    target_country: str = Form(None),
    target_language: str = Form(None),
    db: Session = Depends(get_db)
):
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if survey:
        survey.title = title
        survey.description = description
        survey.form_url = form_url
        survey.category = category
        survey.estimated_time = estimated_time
        survey.reward_amount = reward_amount
        survey.target_responses = target_responses
        survey.target_age_range = target_age_range
        survey.target_education = target_education
        survey.target_field = target_field
        survey.target_status = target_status
        survey.target_country = target_country
        survey.target_language = target_language
        db.commit()
    return RedirectResponse("/publisher", status_code=303)

@app.get("/profile", response_class=HTMLResponse)
def profile_get(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "user": current_user}
    )

@app.post("/profile")
def profile_post(
    request: Request,
    username: str = Form(None),
    email: str = Form(...),
    age_range: str = Form(None),
    education_level: str = Form(None),
    field: str = Form(None),
    status: str = Form(None),
    country: str = Form(None),
    language: str = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current_user.username = username
    current_user.email = email
    current_user.age_range = age_range
    current_user.education_level = education_level
    current_user.field = field
    current_user.status = status
    current_user.country = country
    current_user.language = language

    db.commit()

    return RedirectResponse("/choice", status_code=303)


app.include_router(router)