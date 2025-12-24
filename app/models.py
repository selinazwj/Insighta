from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password = Column(String)
    #created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    surveys = relationship("Survey", back_populates="publisher")


class Survey(Base):
    __tablename__ = "surveys"
    
    id = Column(Integer, primary_key=True, index=True)
    publisher_id = Column(Integer, ForeignKey("users.id"))
    
    # 基本信息
    title = Column(String)
    description = Column(String)
    form_url = Column(String)
    
    # 分类和时长
    category = Column(String)  # research, life, clubs, etc.
    estimated_time = Column(Integer)  # 分钟数
    
    # 奖励设置
    reward_amount = Column(Float)
    target_responses = Column(Integer)
    current_responses = Column(Integer, default=0)
    
    # 状态
    status = Column(String, default="active")  # active, paused, completed
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    publisher = relationship("User", back_populates="surveys")