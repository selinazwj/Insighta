from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey,func
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
#from app.database import Base
Base = declarative_base()

# ======================
# User（填写者 & 发布者）
# ======================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    username = Column(String, nullable=True)

    age_range = Column(String, nullable=True)
    education_level = Column(String, nullable=True)
    field = Column(String, nullable=True)
    status = Column(String, nullable=True)
    country = Column(String, nullable=True)
    language = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系：我发布的 surveys
    surveys = relationship("Survey", back_populates="publisher")
    responses = relationship("Response", back_populates="participant")



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
    image_url = Column(String, nullable=True)        # custom image

    # ===== 奖励与进度 =====
    reward_amount = Column(Float, nullable=False)
    target_responses = Column(Integer, nullable=False)
    current_responses = Column(Integer, default=0)

    # ===== 状态 =====
    status = Column(String, default="draft")  # draft / published / closed
    published_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    publisher = relationship("User", back_populates="surveys")
    responses = relationship("Response", back_populates="survey")


# ======================
# Response（参与者填写记录）
# ======================
class Response(Base):
    __tablename__ = "responses"

    id = Column(Integer, primary_key=True, index=True)

    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    status = Column(String, default="started")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    survey = relationship("Survey", back_populates="responses")
    participant = relationship("User", back_populates="responses")