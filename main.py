from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.database import engine
from app.models import Base
from app.core import router

# 创建数据库表
Base.metadata.create_all(bind=engine)

app = FastAPI()

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册路由
app.include_router(router)
