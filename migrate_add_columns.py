"""
One-time migration: add new columns to users and surveys tables.
Run with: DATABASE_URL=your_url python migrate_add_columns.py
Works with SQLite and PostgreSQL (e.g. production).
"""
import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Set DATABASE_URL and run again.")
    sys.exit(1)

engine = create_engine(DATABASE_URL)
is_sqlite = "sqlite" in DATABASE_URL

def run(conn, sql, ignore_error=None):
    """Run SQL; optionally ignore a specific error (e.g. duplicate column)."""
    try:
        conn.execute(text(sql))
        conn.commit()
        return True
    except Exception as e:
        if ignore_error and (ignore_error in str(e) or "already exists" in str(e).lower() or "duplicate" in str(e).lower()):
            conn.rollback()
            return False
        conn.rollback()
        raise

# Columns we added to users (code expects these names)
USER_NEW_COLUMNS = [
    "state",
    "ethnicity",
    "mental_health_diagnosis",
    "physical_health_diagnosis",
    "sexual_orientation",
    "sport_type",
    "sport_frequency",
    "smoking",
    "cannabis_use",
    "language",
]

SURVEY_NEW_COLUMNS = [
    "target_state",
    "target_ethnicity",
    "target_sexual_orientation",
    "target_mental_health_diagnosis",
    "target_physical_health_diagnosis",
    "target_sport_type",
    "target_sport_frequency",
    "target_smoking",
    "target_cannabis_use",
]

def add_column_if_not_exists(conn, table, col, is_sqlite):
    col_type = "TEXT" if is_sqlite else "VARCHAR"
    if is_sqlite:
        try:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
            conn.commit()
            print(f"  + {table}.{col}")
        except Exception as e:
            conn.rollback()
            if "duplicate column" in str(e).lower():
                print(f"  (skip, exists) {table}.{col}")
            else:
                raise
    else:
        sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
        conn.execute(text(sql))
        conn.commit()
        print(f"  + {table}.{col} (if not exists)")

def try_rename(conn, table, old_name, new_name, is_sqlite):
    try:
        conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}"))
        conn.commit()
        print(f"  renamed {table}.{old_name} -> {new_name}")
    except Exception as e:
        conn.rollback()
        err = str(e).lower()
        if "no such column" in err or "does not exist" in err or "duplicate" in err or "already exists" in err:
            pass
        else:
            raise

def main():
    with engine.connect() as conn:
        # 1) Rename old columns first (so we don't add state then duplicate with rename)
        try_rename(conn, "users", "country", "state", is_sqlite)
        try_rename(conn, "surveys", "target_country", "target_state", is_sqlite)

        print("Migrating users...")
        for col in USER_NEW_COLUMNS:
            add_column_if_not_exists(conn, "users", col, is_sqlite)

        print("Migrating surveys...")
        for col in SURVEY_NEW_COLUMNS:
            add_column_if_not_exists(conn, "surveys", col, is_sqlite)

    print("Done.")

if __name__ == "__main__":
    main()
