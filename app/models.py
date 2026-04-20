from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func, Boolean, JSON
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

    # Core profile fields
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

    # Student segmentation
    student_status = Column(String, nullable=True)
    year_in_school = Column(String, nullable=True)
    international_domestic = Column(String, nullable=True)
    experience_tags = Column(String, nullable=True)
    participation_format = Column(String, nullable=True)
    device_type = Column(String, nullable=True)

    # OAuth fields
    oauth_provider = Column(String, nullable=True)   # "google" | "linkedin" | None
    oauth_id = Column(String, nullable=True)          # provider's unique user id

    # Stripe fields
    stripe_account_id = Column(String, nullable=True)
    stripe_onboarding_complete = Column(String, default="false")
    pending_earnings = Column(Float, default=0.0)
    total_withdrawn = Column(Float, default=0.0)

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

    # Core targeting fields
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

    # Extended targeting fields
    target_student_status = Column(String, nullable=True)
    target_year_in_school = Column(String, nullable=True)
    target_international_domestic = Column(String, nullable=True)
    target_experience_tags = Column(String, nullable=True)
    target_participation_format = Column(String, nullable=True)
    target_device = Column(String, nullable=True)
    urgency_level = Column(String, nullable=True)
    incentive_type = Column(String, nullable=True)

    # Survey metadata
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    form_url = Column(String, nullable=False)
    task_type = Column(String, default="survey", nullable=True)
    category = Column(String, nullable=False)
    estimated_time = Column(Integer, nullable=False)
    image_url = Column(String, nullable=True)

    # Reward & progress
    reward_amount = Column(Float, nullable=False)
    total_budget = Column(Float, nullable=True)
    per_person_gross = Column(Float, nullable=True)
    commission_rate = Column(Float, nullable=True)
    target_responses = Column(Integer, nullable=False)
    current_responses = Column(Integer, default=0)

    # Status & timestamps
    status = Column(String, default="draft")
    published_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Stripe payment status
    payment_status = Column(String, default="unpaid")
    stripe_payment_intent_id = Column(String, nullable=True)

    publisher = relationship("User", back_populates="surveys")
    responses = relationship("Response", back_populates="survey")
    questions = relationship("Question", back_populates="survey")


# ======================
# Notification
# ======================
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    publisher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False)
    participant_email = Column(String, nullable=True)
    survey_title = Column(String, nullable=True)
    task_type = Column(String, default="survey")
    status = Column(String, default="pending")  # pending / accepted / rejected
    created_at = Column(DateTime, default=datetime.utcnow)


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

    # Stripe payout status
    payout_status = Column(String, default="pending")
    payout_amount = Column(Float, nullable=True)
    stripe_transfer_id = Column(String, nullable=True)

    survey = relationship("Survey", back_populates="responses")
    participant = relationship("User", back_populates="responses")
    answers = relationship("Answer", back_populates="response")


# ======================
# Feedback
# ======================
class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    category = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(String, nullable=False)

    status = Column(String, default="pending")
    credit_amount = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    user = relationship("User", backref="feedbacks")


# ======================
# Email verification / password reset code
# ======================
class EmailVerificationCode(Base):
    __tablename__ = "email_verification_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    purpose = Column(String, index=True, nullable=False)
    code = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)

# ======================
# Question
# ======================
class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False)
    question_text = Column(String, nullable=False)
    question_type = Column(String, nullable=False)
    # single / multiple / text / scale / dropdown
    options = Column(JSON, nullable=True)
    is_required = Column(Boolean, default=True)
    order_index = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    survey = relationship("Survey", back_populates="questions")
    answers = relationship("Answer", back_populates="question")


# ======================
# Answer
# ======================
class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    answer_value = Column(JSON, nullable=False)
    # 单选: "A"
    # 多选: ["A", "B"]
    # 文字: "我觉得..."
    # 量表: 4
    created_at = Column(DateTime, default=datetime.utcnow)

    response = relationship("Response", back_populates="answers")
    question = relationship("Question", back_populates="answers")