"""Studies persistence models."""

from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

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
    
    # ive added these so researchers pick who can fill the survey
    required_occupation = Column(String, nullable=True)
    required_verification_tier = Column(String, default="tier_3")
    # ive added this for the email-domain feature — comma separated allowed domains
    # (no @, lowercase) e.g. "bu.edu,gmail.com". blank means any email is fine
    required_email_domains = Column(String, nullable=True)
    
    # Extended targeting fields
    target_student_status = Column(String, nullable=True)
    target_year_in_school = Column(String, nullable=True)
    target_international_domestic = Column(String, nullable=True)
    target_experience_tags = Column(String, nullable=True)
    target_participation_format = Column(String, nullable=True)
    target_device = Column(String, nullable=True)
    target_income_level = Column(String, nullable=True)
    target_lifestyle_tags = Column(String, nullable=True)
    target_niche_requirements = Column(String, nullable=True)
    participant_benefits = Column(String, nullable=True)
    availability_slots = Column(String, nullable=True)
    interview_location = Column(String, nullable=True)
    session_count = Column(Integer, nullable=True)
    sessions_per_week = Column(Integer, nullable=True)
    urgency_level = Column(String, nullable=True)
    incentive_type = Column(String, nullable=True)
    raffle_prize_type = Column(String, nullable=True)

    # Survey metadata
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    form_url = Column(String, nullable=False)
    task_type = Column(String, default="survey", nullable=True)
    category = Column(String, nullable=False)
    estimated_time = Column(Integer, nullable=False)
    image_url = Column(String, nullable=True)
    share_slug = Column(String, unique=True, index=True, nullable=True)
    publish_token = Column(String, unique=True, index=True, nullable=True)

    # Reward & progress
    reward_amount = Column(Float, nullable=False)
    admin_display_reward_amount = Column(Float, nullable=True)
    total_budget = Column(Float, nullable=True)
    per_person_gross = Column(Float, nullable=True)
    commission_rate = Column(Float, nullable=True)
    pricing_snapshot = Column(JSON, nullable=True)
    funding_source = Column(String, nullable=True)
    refund_destination = Column(String, nullable=True)
    target_responses = Column(Integer, nullable=False)
    current_responses = Column(Integer, default=0)

    # Status & timestamps
    status = Column(String, default="draft")
    published_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    deadline_at = Column(DateTime, nullable=True)
    close_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Stripe payment status
    payment_status = Column(String, default="unpaid")
    stripe_payment_intent_id = Column(String, nullable=True)
    stripe_funding_payment_intent_id = Column(String, nullable=True)

    # Quality auto-filter (publisher default for Results / Excel quality checks)
    quality_auto_filter_enabled = Column(Boolean, default=False)
    quality_auto_filter_min_score = Column(Float, default=80.0)

    publisher = relationship("User", back_populates="surveys")
    responses = relationship("Response", back_populates="survey")
    questions = relationship("Question", back_populates="survey")


class StudySettlement(Base):
    __tablename__ = "study_settlements"

    id = Column(Integer, primary_key=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), unique=True, nullable=False, index=True)
    status = Column(String, nullable=False, default="waiting_review", index=True)
    close_reason = Column(String, nullable=False)
    refund_destination = Column(String, nullable=True)
    refund_gross_cents = Column(Integer, nullable=True)
    pricing_snapshot = Column(JSON, nullable=False)
    qualified_count = Column(Integer, nullable=True)
    refund_amount_cents = Column(Integer, nullable=True)
    currency = Column(String, nullable=False, default="usd")
    payment_intent_id = Column(String, nullable=True)
    stripe_refund_id = Column(String, nullable=True, unique=True)
    qualified_response_ids = Column(JSON, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    frozen_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
