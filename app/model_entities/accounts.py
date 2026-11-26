"""Accounts persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone_number = Column(String, nullable=True)

    # Core profile fields
    age_range = Column(String, nullable=True)
    birth_year = Column(String, nullable=True)
    birth_month = Column(String, nullable=True)
    profile_description = Column(String, nullable=True)
    education_level = Column(String, nullable=True)
    field = Column(String, nullable=True)
    status = Column(String, nullable=True)
    state = Column(String, nullable=True)
    current_country = Column(String, nullable=True)
    current_province = Column(String, nullable=True)
    current_city = Column(String, nullable=True)
    origin_country = Column(String, nullable=True)
    origin_province = Column(String, nullable=True)
    origin_city = Column(String, nullable=True)
    race = Column(String, nullable=True)
    ethnicity = Column(String, nullable=True)
    mental_health_diagnosis = Column(String, nullable=True)
    physical_health_diagnosis = Column(String, nullable=True)
    sexual_orientation = Column(String, nullable=True)
    sport_type = Column(String, nullable=True)
    sport_frequency = Column(String, nullable=True)
    smoking = Column(String, nullable=True)
    cannabis_use = Column(String, nullable=True)
    language = Column(String, nullable=True)
    
    # ive added these for the verification feature (Phase 1)
    occupation = Column(String, nullable=True)
    verification_status = Column(String, default="unverified")
    verified_at = Column(DateTime, nullable=True)
    verified_tier = Column(String, nullable=True)
    # ive added this for the email-domain feature — the email the participant proved via OTP
    # (may differ from their account email, e.g. they verified their @bu.edu address)
    verified_email = Column(String, nullable=True)

    # Student segmentation
    student_status = Column(String, nullable=True)
    year_in_school = Column(String, nullable=True)
    international_domestic = Column(String, nullable=True)
    experience_tags = Column(String, nullable=True)
    income_level = Column(String, nullable=True)
    lifestyle_tags = Column(String, nullable=True)
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
    welcome_email_sent_at = Column(DateTime, nullable=True)
    welcome_email_role = Column(String, nullable=True)

    # Referral
    referral_code = Column(String, unique=True, index=True, nullable=True)
    invited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    surveys = relationship("Survey", back_populates="publisher")
    responses = relationship("Response", back_populates="participant")
