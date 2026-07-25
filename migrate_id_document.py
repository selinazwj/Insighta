"""
One-time migration: add id_document_path to users (verification phase 2c).
Run with: DATABASE_URL=your_url python migrate_id_document.py
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


def add_column_if_not_exists(conn, table, col, col_type="VARCHAR"):
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
        print(f"  + {table}.{col} (if not exists)")


def main():
    with engine.connect() as conn:
        print("Adding ID document field to users...")
        add_column_if_not_exists(conn, "users", "id_document_path", "VARCHAR")
    print("Done!")


if __name__ == "__main__":
    main()
