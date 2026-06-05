"""
Create/upgrade quality-check related tables and columns.
Run with:
    python migrate_quality_checks.py
Uses DATABASE_URL if set, otherwise local SQLite (survey.db).
"""
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

from app.models import Base


RESPONSE_COLUMNS = [
    ("client_ip", "VARCHAR"),
    ("user_agent", "VARCHAR"),
    ("device_fingerprint", "VARCHAR"),
]

SURVEY_AUTO_FILTER_COLUMNS = [
    ("quality_auto_filter_enabled", "BOOLEAN DEFAULT 0"),
    ("quality_auto_filter_min_score", "FLOAT DEFAULT 80"),
]


def main():
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL") or "sqlite:///./survey.db"
    is_sqlite = "sqlite" in database_url

    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    existing_response_cols = {c["name"] for c in inspector.get_columns("responses")} if inspector.has_table("responses") else set()
    existing_survey_cols = {c["name"] for c in inspector.get_columns("surveys")} if inspector.has_table("surveys") else set()

    with engine.begin() as conn:
        for col, col_type in RESPONSE_COLUMNS:
            if col in existing_response_cols:
                continue
            sql = f"ALTER TABLE responses ADD COLUMN {col} {col_type}"
            if not is_sqlite:
                sql = f"ALTER TABLE responses ADD COLUMN IF NOT EXISTS {col} {col_type}"
            try:
                conn.execute(text(sql))
                print(f"Added responses.{col}")
            except Exception as exc:
                if "duplicate column" in str(exc).lower() or "already exists" in str(exc).lower():
                    print(f"Skip responses.{col} (already exists)")
                else:
                    raise

        for col, col_type in SURVEY_AUTO_FILTER_COLUMNS:
            if col in existing_survey_cols:
                continue
            sql = f"ALTER TABLE surveys ADD COLUMN {col} {col_type}"
            if not is_sqlite:
                sql = f"ALTER TABLE surveys ADD COLUMN IF NOT EXISTS {col} {col_type}"
            try:
                conn.execute(text(sql))
                print(f"Added surveys.{col}")
            except Exception as exc:
                if "duplicate column" in str(exc).lower() or "already exists" in str(exc).lower():
                    print(f"Skip surveys.{col} (already exists)")
                else:
                    raise

    print("Done! Quality-check schema is ready.")


if __name__ == "__main__":
    main()
