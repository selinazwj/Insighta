import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()  # 加载 .env 文件

# ✅ 先读取环境变量
DATABASE_URL = os.environ.get("DATABASE_URL")

# ✅ 根据 DATABASE_URL 判断本地/远程
if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    # Supabase/Postgres 需要 SSL
    engine = create_engine(DATABASE_URL, connect_args={"sslmode": "require"})
else:
    # 本地 SQLite 测试环境
    if not DATABASE_URL:
        DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# ✅ 创建 Session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ✅ 声明 Base
Base = declarative_base()

# ✅ 数据库依赖
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
