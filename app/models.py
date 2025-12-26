from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


# ======================
# User（填写者 & 发布者）
# ======================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)

    # ===== 用户画像（用于匹配 survey）=====
    age_range = Column(String, nullable=True)        # "18-24"
    education_level = Column(String, nullable=True)  # Undergraduate / Graduate
    field = Column(String, nullable=True)            # CS / Econ / Bio
    status = Column(String, nullable=True)           # student / working
    country = Column(String, nullable=True)
    language = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系：我发布的 surveys
    surveys = relationship("Survey", back_populates="publisher")


# ======================
# Survey
# ======================
class Survey(Base):
    __tablename__ = "surveys"

    id = Column(Integer, primary_key=True, index=True)

    # 发布者
    publisher_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # ===== 定向投放条件 =====
    target_age_range = Column(String, nullable=True)
    target_education = Column(String, nullable=True)
    target_field = Column(String, nullable=True)
    target_status = Column(String, nullable=True)
    target_country = Column(String, nullable=True)
    target_language = Column(String, nullable=True)

    # ===== 基本信息 =====
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    form_url = Column(String, nullable=False)

    category = Column(String, nullable=False)        # research / life / clubs
    estimated_time = Column(Integer, nullable=False) # minutes

    # ===== 奖励与进度 =====
    reward_amount = Column(Float, nullable=False)
    target_responses = Column(Integer, nullable=False)
    current_responses = Column(Integer, default=0)

    # ===== 状态 =====
    status = Column(String, default="active")  # active / paused / completed
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    publisher = relationship("User", back_populates="surveys")

  # 新增 target audience 字段
    target_age_range = Column(String, nullable=True)
    target_education = Column(String, nullable=True)
    target_country = Column(String, nullable=True)