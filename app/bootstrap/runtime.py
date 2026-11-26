"""Bootstrap / runtime."""

import os
from fastapi import APIRouter
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
import logging


from app.observability import configure_logging

configure_logging()

logger = logging.getLogger("insighta.api")


app = FastAPI(title="Insighta")


templates = Jinja2Templates(directory="app/templates")


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


router = APIRouter()


templates.env.globals["staging_login"] = lambda: os.environ.get("INSIGHTA_ENV") == "staging"

__all__ = ['logger', 'app', 'templates', 'pwd_context', 'router']
