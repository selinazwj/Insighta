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
    db: Session = Depends(get_db)
):
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    password = password[:72]  # bcrypt 72 bytes 限制
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
    if not user or not pwd_context.verify(password, user.password):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    return RedirectResponse("/dashboard", status_code=303)


@router.get("/dashboard")
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})
