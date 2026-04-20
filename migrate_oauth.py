"""
Migration: add oauth_provider and oauth_id columns to the users table.
Run once with:
    DATABASE_URL=your_url python migrate_oauth.py
Works with both SQLite (local) and PostgreSQL (production).
"""
import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: Set DATABASE_URL and run again.")
    sys.exit(1)

engine = create_engine(DATABASE_URL)
is_sqlite = "sqlite" in DATABASE_URL


def add_column(conn, table, col, col_type="VARCHAR"):
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
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        conn.commit()
        print(f"  + {table}.{col}")


def main():
    with engine.connect() as conn:
        print("Adding OAuth columns to users table...")
        add_column(conn, "users", "oauth_provider", "VARCHAR")
        add_column(conn, "users", "oauth_id", "VARCHAR")
    print("Done! OAuth columns are ready.")


if __name__ == "__main__":
    main()
