from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

Base = declarative_base()


# ======================
# User (filler & publisher)
# ======================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    username = Column(String, nullable=True)

    # 原有字段
    age_range = Column(String, nullable=True)
    education_level = Column(String, nullable=True)
    field = Column(String, nullable=True)
    status = Column(String, nullable=True)
    state = Column(String, nullable=True)
    ethnicity = Column(String, nullable=True)
    mental_health_diagnosis = Column(String, nullable=True)
    physical_health_diagnosis = Column(String, nullable=True)
    sexual_orientation = Column(String, nullable=True)
    sport_type = Column(String, nullable=True)
    sport_frequency = Column(String, nullable=True)
    smoking = Column(String, nullable=True)
    cannabis_use = Column(String, nullable=True)
    language = Column(String, nullable=True)

    # ── 新增字段 ──────────────────────────────────────────────
    # 学生细分
    student_status = Column(String, nullable=True)
    # undergrad / grad / non-student

    year_in_school = Column(String, nullable=True)
    # Freshman / Sophomore / Junior / Senior / Graduate Year 1 / Graduate Year 2+ / N/A

    international_domestic = Column(String, nullable=True)
    # Domestic / International

    # 经历标签（逗号分隔，可多选）
    # 例如: "startup_experience,club_leadership"
    experience_tags = Column(String, nullable=True)

    # 参与偏好
    participation_format = Column(String, nullable=True)
    # online / in_person / both

    device_type = Column(String, nullable=True)
    # laptop / mobile / any
    # ──────────────────────────────────────────────────────────

    created_at = Column(DateTime, default=datetime.utcnow)

    surveys = relationship("Survey", back_populates="publisher")
    responses = relationship("Response", back_populates="participant")


# ======================
# Survey
# ======================
class Survey(Base):
    __tablename__ = "surveys"

    id = Column(Integer, primary_key=True, index=True)

    publisher_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # 原有 target 字段
    target_age_range = Column(String, nullable=True)
    target_education_min = Column(Integer, nullable=True)
    target_education_max = Column(Integer, nullable=True)
    target_field = Column(String, nullable=True)
    target_status = Column(String, nullable=True)
    target_state = Column(String, nullable=True)
    target_language = Column(String, nullable=True)
    target_ethnicity = Column(String, nullable=True)
    target_sexual_orientation = Column(String, nullable=True)
    target_mental_health_diagnosis = Column(String, nullable=True)
    target_physical_health_diagnosis = Column(String, nullable=True)
    target_sport_type = Column(String, nullable=True)
    target_sport_frequency = Column(String, nullable=True)
    target_smoking = Column(String, nullable=True)
    target_cannabis_use = Column(String, nullable=True)

    # ── 新增 target 字段 ──────────────────────────────────────
    target_student_status = Column(String, nullable=True)
    # undergrad / grad / non-student / all

    target_year_in_school = Column(String, nullable=True)
    # Freshman / Sophomore / ... / all

    target_international_domestic = Column(String, nullable=True)
    # Domestic / International / all

    target_experience_tags = Column(String, nullable=True)
    # 逗号分隔，只要用户有其中任意一个 tag 就匹配
    # 例如: "startup_experience,club_leadership"

    target_participation_format = Column(String, nullable=True)
    # online / in_person / both / all

    target_device = Column(String, nullable=True)
    # laptop / mobile / any

    urgency_level = Column(String, nullable=True)
    # flexible / within_1_week / within_3_days

    incentive_type = Column(String, nullable=True)
    # cash / gift_card / raffle / volunteer
    # ──────────────────────────────────────────────────────────

    # 基本信息
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    form_url = Column(String, nullable=False)

    task_type = Column(String, nullable=True)
    # survey / interview / product_testing / story_collection / in_person_experiment

    category = Column(String, nullable=False)
    estimated_time = Column(Integer, nullable=False)
    image_url = Column(String, nullable=True)

    # 奖励 & 进度
    reward_amount = Column(Float, nullable=False)
    target_responses = Column(Integer, nullable=False)
    current_responses = Column(Integer, default=0)

    # 状态
    status = Column(String, default="draft")
    published_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    publisher = relationship("User", back_populates="surveys")
    responses = relationship("Response", back_populates="survey")


# ======================
# Response
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