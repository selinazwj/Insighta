"""Start the isolated Render pricing test environment with migrations applied."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def configure_staging(env):
    if env.get("INSIGHTA_ENV") != "staging":
        raise RuntimeError("This entrypoint is only for INSIGHTA_ENV=staging.")
    database_url = env.get("DATABASE_URL", "")
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise RuntimeError("Staging requires its dedicated PostgreSQL database.")
    if not parsed.path.lstrip("/").startswith("insighta_pricing_staging"):
        raise RuntimeError("Refusing to connect staging to an unrelated database.")
    for key in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"):
        value = env.get(key, "")
        if value and not value.startswith(("sk_test_", "pk_test_", "rk_test_")):
            raise RuntimeError("Staging accepts Stripe test-mode keys only.")
    origin = env.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not origin.startswith("https://"):
        raise RuntimeError("RENDER_EXTERNAL_URL must identify this test service.")
    env["BASE_URL"] = origin
    env["SEO_SITE_URL"] = origin
    env["SEO_INDEX_STUDIES"] = "false"
    env["INSIGHTA_AUTO_SCHEMA_BOOTSTRAP"] = "1"


def seed_researcher_account(db, env):
    """Provision an optional isolated test identity; never reset an existing user."""
    configure_staging(env)
    email = (env.get("STAGING_RESEARCHER_EMAIL") or "").strip().lower()
    password_hash = env.get("STAGING_RESEARCHER_PASSWORD_HASH") or ""
    if not email and not password_hash:
        return False
    if not email.endswith(".invalid") or "@" not in email or len(password_hash) != 60:
        raise RuntimeError("Staging researcher requires a .invalid test email and bcrypt password hash.")
    from passlib.context import CryptContext
    if CryptContext(schemes=["bcrypt"]).identify(password_hash) != "bcrypt":
        raise RuntimeError("Staging researcher password hash must use bcrypt.")
    from app.models import User
    if db.query(User).filter_by(email=email).first():
        return False
    from datetime import datetime
    db.add(User(email=email, password=password_hash, username="Researcher Preview",
        first_name="Test", last_name="Researcher", birth_year="1990", birth_month="1",
        education_level="Graduate", field="Psychology", status="Graduate student",
        state="Massachusetts", language="English", participation_format="Any format",
        profile_description="Isolated staging test account. Fictional profile for product testing.",
        welcome_email_sent_at=datetime.utcnow(), welcome_email_role="researcher"))
    db.commit()
    return True


def main():
    configure_staging(os.environ)
    os.chdir(ROOT)
    from app.database import engine
    from app.models import Base
    from app.ai_growth import models as growth_models  # noqa: F401
    # Legacy migrations assume core tables already exist. Initialize only the
    # dedicated empty staging database, then run the full migration history.
    Base.metadata.create_all(bind=engine)
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    from app.database import SessionLocal
    with SessionLocal() as db:
        seed_researcher_account(db, os.environ)
    engine.dispose()
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "api.main:app",
                            "--host", "0.0.0.0", "--port", os.environ.get("PORT", "10000")])


if __name__ == "__main__":
    main()
