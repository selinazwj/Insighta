"""Compose the ASGI application while preserving the existing deployment entry."""

# Settings must load before database clients and feature integrations.
from app.bootstrap import settings
# Preserve integration import order before the compatibility schema bootstrap.
# This module supplies only the old import surface, not application logic.
from app.bootstrap import legacy_imports as _legacy_imports

import os
import stripe
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from app.auth_session import SignedSessionMiddleware
from app.ai_growth.routes import router as ai_growth_router
from app.discovery.router import router as discovery_router
from app.database import get_db
from app.wallet_routes import register_wallet_routes
from app.niche_routes import register_niche_routes
from app.bootstrap.runtime import app, router, templates
from app.bootstrap import lifecycle, schema
from app.web import middleware, routing
from app.features.auth.dependencies import get_current_user
from app.features.admin.access import _admin_key_matches


def configure_application():
    """Perform the original application setup once when this module is imported."""
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(SignedSessionMiddleware)
    app.include_router(ai_growth_router)
    app.include_router(discovery_router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    app.middleware("http")(middleware.log_request_timing)
    app.middleware("http")(middleware.apply_seo_and_delivery_headers)
    schema.initialize_database()

    # Preserve the existing Stripe snapshot API contract.
    stripe.api_version = "2025-03-31.basil"
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

    app.on_event("shutdown")(lifecycle.stop_settlement_worker)
    app.on_event("startup")(lifecycle.start_survey_followup_worker)
    routing.register_routes(app)
    app.include_router(router)
    register_wallet_routes(
        app, templates, get_db, get_current_user,
        _admin_key_matches, lambda: settings.BASE_URL,
    )
    register_niche_routes(
        app, templates, get_db, get_current_user,
        _admin_key_matches, lambda: settings.BASE_URL,
    )
    return app


configure_application()
