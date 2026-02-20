from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey,func
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
from sqlalchemy import Integer
#from app.database import Base
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

    age_range = Column(String, nullable=True)
    education_level = Column(String, nullable=True)
    field = Column(String, nullable=True)
    status = Column(String, nullable=True)
    state = Column(String, nullable=True)
    ethnicity = Column(String, nullable=True)
    mental_health_diagnosis = Column(String, nullable=True)   # Yes / No / Prefer not to say
    physical_health_diagnosis = Column(String, nullable=True) # Yes / No / Prefer not to say
    sexual_orientation = Column(String, nullable=True)
    sport_type = Column(String, nullable=True)
    sport_frequency = Column(String, nullable=True)
    smoking = Column(String, nullable=True)
    cannabis_use = Column(String, nullable=True)
    language = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    surveys = relationship("Survey", back_populates="publisher")
    responses = relationship("Response", back_populates="participant")



# ======================
# Survey
# ======================
class Survey(Base):
    __tablename__ = "surveys"

    id = Column(Integer, primary_key=True, index=True)

    # Publisher
    publisher_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Target audience
    target_age_range = Column(String, nullable=True)

    target_education_min = Column(Integer, nullable=True)
    target_education_max = Column(Integer, nullable=True)

    target_field = Column(String, nullable=True)
    target_status = Column(String, nullable=True)
    target_state = Column(String, nullable=True)
    target_language = Column(String, nullable=True)
    # Advanced (from registration profile)
    target_ethnicity = Column(String, nullable=True)
    target_sexual_orientation = Column(String, nullable=True)
    target_mental_health_diagnosis = Column(String, nullable=True)
    target_physical_health_diagnosis = Column(String, nullable=True)
    target_sport_type = Column(String, nullable=True)
    target_sport_frequency = Column(String, nullable=True)
    target_smoking = Column(String, nullable=True)
    target_cannabis_use = Column(String, nullable=True)

    # Basic info
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    form_url = Column(String, nullable=False)

    category = Column(String, nullable=False)        # research / life / clubs
    estimated_time = Column(Integer, nullable=False) # minutes
    image_url = Column(String, nullable=True)        # custom image

    # Reward & progress
    reward_amount = Column(Float, nullable=False)
    target_responses = Column(Integer, nullable=False)
    current_responses = Column(Integer, default=0)

    # Status (draft / published / closed)
    status = Column(String, default="draft")
    published_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    publisher = relationship("User", back_populates="surveys")
    responses = relationship("Response", back_populates="survey")


# ======================
# Response (participant completion record)
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