from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from .database import SessionLocal
from .models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")  # 项目根 templates

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 首页 → 显示主界面 + 弹窗
@router.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.post("/register")
def register_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    db: Session = Depends(get_db)
):

    # 密码一致性验证
    if password != confirm:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Passwords do not match.",
                "show": "register"
            }
        )

    # 密码强度验证
    import re
    pattern = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"

    if not re.match(pattern, password):
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Password must be 8+ chars, include upper, lower and number.",
                "show": "register"
            }
        )

    # 检查 email 是否已存在
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Email already registered.",
                "show": "register"
            }
        )

    # bcrypt 限制密码最大长度 72 bytes
    password = password[:72]
    hashed = pwd_context.hash(password)

    new_user = User(email=email, password=hashed)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return RedirectResponse("/", status_code=303)

@router.post("/login")
def login_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):

    user = db.query(User).filter(User.email == email).first()

    if not user:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Invalid email or password.",
                "show": "login"
            }
        )

    if not pwd_context.verify(password, user.password):
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Invalid email or password.",
                "show": "login"
            }
        )

    return RedirectResponse("/dashboard", status_code=303)

@router.get("/dashboard")
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@router.get("/surveys/{category}")
def show_category(request: Request, category: str):

    # 模拟数据库里的问卷
    sample_surveys = {
        "research": [
            {"title": "AI Adoption Study", "desc": "Help us understand how students use AI tools."},
            {"title": "Campus Learning Behaviors", "desc": "A quick study on academic habits."}
        ],
        "lifestyle": [
            {"title": "Daily Habits Survey", "desc": "Share your lifestyle habits anonymously."},
            {"title": "Food Preferences Survey", "desc": "What do students eat the most?"}
        ],
        "clubs": [
            {"title": "International Club Feedback", "desc": "Help us improve our events."},
            {"title": "Student Org Interests", "desc": "Which clubs do you want on campus?"}
        ]
    }

    return templates.TemplateResponse(
        "category.html",
        {
            "request": request,
            "category": category.capitalize(),
            "surveys": sample_surveys.get(category, [])
        }
    )
