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

    # 学生细分
    student_status = Column(String, nullable=True)
    year_in_school = Column(String, nullable=True)
    international_domestic = Column(String, nullable=True)
    experience_tags = Column(String, nullable=True)
    participation_format = Column(String, nullable=True)
    device_type = Column(String, nullable=True)

    # ── Stripe 相关 ──────────────────────────────────────────
    stripe_account_id = Column(String, nullable=True)
    # Participant 的 Stripe Connect account ID (acct_...)

    stripe_onboarding_complete = Column(String, default="false")
    # "true" / "false"

    pending_earnings = Column(Float, default=0.0)
    # 已完成问卷但还未提现的金额

    total_withdrawn = Column(Float, default=0.0)
    # 累计已提现金额
    # ─────────────────────────────────────────────────────────

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

    # 新增 target 字段
    target_student_status = Column(String, nullable=True)
    target_year_in_school = Column(String, nullable=True)
    target_international_domestic = Column(String, nullable=True)
    target_experience_tags = Column(String, nullable=True)
    target_participation_format = Column(String, nullable=True)
    target_device = Column(String, nullable=True)
    urgency_level = Column(String, nullable=True)
    incentive_type = Column(String, nullable=True)

    # 基본信息
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    form_url = Column(String, nullable=False)
    task_type = Column(String, nullable=True)
    category = Column(String, nullable=False)
    estimated_time = Column(Integer, nullable=False)
    image_url = Column(String, nullable=True)

    # 奖励 & 进度
    reward_amount = Column(Float, nullable=False)
    # per person 税后金额（participant 看到的）

    total_budget = Column(Float, nullable=True)
    # publisher 支付的总金额（含平台抽成）

    per_person_gross = Column(Float, nullable=True)
    # publisher 每人支付的金额（含平台抽成）

    commission_rate = Column(Float, nullable=True)
    # 平台抽成比例 0.15 / 0.20 / 0.25

    target_responses = Column(Integer, nullable=False)
    current_responses = Column(Integer, default=0)

    # 状态
    status = Column(String, default="draft")
    published_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # ── Stripe 支付状态 ───────────────────────────────────────
    payment_status = Column(String, default="unpaid")
    # unpaid / paid / refunded

    stripe_payment_intent_id = Column(String, nullable=True)
    # Stripe PaymentIntent 或 Checkout Session ID
    # ─────────────────────────────────────────────────────────

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

    # ── Stripe 打款状态 ───────────────────────────────────────
    payout_status = Column(String, default="pending")
    # pending / paid / failed

    payout_amount = Column(Float, nullable=True)
    # 实际打给 participant 的金额

    stripe_transfer_id = Column(String, nullable=True)
    # Stripe Transfer ID
    # ─────────────────────────────────────────────────────────

    survey = relationship("Survey", back_populates="responses")
    participant = relationship("User", back_populates="responses")


# ======================
# Feedback
# ======================
class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    category = Column(String, nullable=False)
    # bug / feature_request / ux / general / other

    title = Column(String, nullable=False)
    content = Column(String, nullable=False)

    status = Column(String, default="pending")
    # pending / reviewed / credited / rejected

    credit_amount = Column(Float, nullable=True)
    # 审核后发放的金额

    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    user = relationship("User", backref="feedbacks")