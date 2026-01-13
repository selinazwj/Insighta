# app/database.py

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1️⃣ 从环境变量读取 DATABASE_URL
# 在 Render 上，请在 Environment Variables 里设置：
# DATABASE_URL=postgresql://postgres:yourpassword@db.gdqxveuougniwuztyltc.supabase.co:5432/postgres
DATABASE_URL = os.environ.get("DATABASE_URL")

# 2️⃣ 创建 SQLAlchemy engine
if DATABASE_URL:
    # 使用 PostgreSQL/Supabase，需要 SSL
    engine = create_engine(DATABASE_URL, connect_args={"sslmode": "require"})
else:
    # 使用本地 SQLite 数据库进行测试
    DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# 3️⃣ 创建 Session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4️⃣ Base class
Base = declarative_base()

# 5️⃣ 数据库依赖，用于 FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
