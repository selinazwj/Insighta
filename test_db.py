# app/database.py

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Read DATABASE_URL from environment (e.g. on Render set in Environment Variables)
# DATABASE_URL=postgresql://postgres:yourpassword@host:5432/postgres
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set!")

# Create SQLAlchemy engine (Supabase requires SSL)
engine = create_engine(DATABASE_URL, connect_args={"sslmode": "require"})

# Session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class
Base = declarative_base()

# DB dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
