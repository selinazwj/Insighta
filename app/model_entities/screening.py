"""Screening persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

class ScreeningQuestion(Base):
    __tablename__ = "screening_questions"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    attribute = Column(String, nullable=False)
    prompt = Column(Text, nullable=False)
    question_type = Column(String, nullable=False, default="single")
    options = Column(JSON, nullable=False)
    accepted_answers = Column(JSON, nullable=False)
    rationale = Column(Text, nullable=True)
    sensitivity = Column(String, nullable=False, default="low")
    source = Column(String, nullable=False, default="rules")  # rules | ai_assisted | researcher_edited
    status = Column(String, nullable=False, default="draft")  # draft | approved | retired
    order_index = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    survey = relationship("Survey", backref="screening_questions")


class ScreeningAttempt(Base):
    __tablename__ = "screening_attempts"
    __table_args__ = (
        UniqueConstraint(
            "survey_id",
            "participant_id",
            "version",
            name="uq_screening_attempt_participant_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    answers = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="started")  # started | passed | failed | review
    risk_score = Column(Float, nullable=False, default=0.0)
    reasons = Column(JSON, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    survey = relationship("Survey", backref="screening_attempts")
    participant = relationship("User", backref="screening_attempts")
