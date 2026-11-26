"""Insighta ASGI entry point (uvicorn and Vercel).

HTTP behavior lives in app.features; assembly lives in app.bootstrap.
Legacy imports from api.main remain supported for existing scripts and tests.
"""

from app.bootstrap.application import app
from app.bootstrap.legacy_api import install_legacy_api

install_legacy_api(__name__)
