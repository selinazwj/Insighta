"""
ive made this migration to add verification columns to users and surveys.
run with: python migrate_verification.py
"""


import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# pick whichever db url is set, fallback to local sqlite
db_url = os.environ.get("DATABASE_URL", "sqlite:///./survey.db")
engine = create_engine(db_url)
is_sqlite = "sqlite" in db_url

# new columns ive added for verification
user_cols = [
    ("occupation", "VARCHAR"),
    ("verification_status", "VARCHAR DEFAULT 'unverified'"),
    ("verified_at", "DATETIME"),
    ("verified_tier", "VARCHAR"),
]

survey_cols = [
    ("required_occupation", "VARCHAR"),
    ("required_verification_tier", "VARCHAR DEFAULT 'tier_3'"),
]


def add_col(conn, table, col_name, col_type):
    # try adding the column, skip if it already exists
    try:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
        conn.commit()
        print(f"  added {table}.{col_name}")
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "already exists" in msg:
            print(f"  skipped {table}.{col_name} (already there)")
            conn.rollback()
        else:
            conn.rollback()
            raise


def main():
    print(f"migrating db: {db_url}")
    with engine.connect() as conn:
        print("users table:")
        for name, typ in user_cols:
            add_col(conn, "users", name, typ)

        print("surveys table:")
        for name, typ in survey_cols:
            add_col(conn, "surveys", name, typ)

    print("done.")


if __name__ == "__main__":
    main()