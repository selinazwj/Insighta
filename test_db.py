import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()  # 读取 .env

DATABASE_URL = os.getenv("DATABASE_URL")

print("DATABASE_URL:", DATABASE_URL)

engine = create_engine(
    DATABASE_URL,
    connect_args={"sslmode": "require"}
)

with engine.connect() as conn:
    result = conn.execute(text("select 1"))
    print("DB RESULT:", result.fetchone())
