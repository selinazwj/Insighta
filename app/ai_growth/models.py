"""
AI Growth data models for Insighta.

These tables are intentionally additive. They do not mutate existing User,
Survey, Response, Question, or Answer tables, which makes the feature safe to
roll out and easy to roll back.
"""

from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models import Base


class JumpEvent(Base):
    """Tracks every task entry/jump event for funnel analysis and prediction."""

    __tablename__ = "jump_events"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=False, index=True)

    source = Column(String, default="dashboard", index=True)
    destination_type = Column(String, nullable=False)  # builtin / external / interview
    destination_url_hash = Column(String, nullable=True)

    token_hash = Column(String, unique=True, index=True, nullable=False)
    token_expires_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow() + timedelta(days=7))

    status = Column(String, default="clicked", index=True)  # clicked / returned / completed / expired / blocked
    clicked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    returned_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    user_agent = Column(Text, nullable=True)
    ip_hash = Column(String, nullable=True)
    metadata_json = Column(JSON, nullable=True)

    survey = relationship("Survey", backref="jump_events")
    participant = relationship("User", backref="jump_events")
    response = relationship("Response", backref="jump_events")


class RespondentPrediction(Base):
    """Caches per-survey per-participant completion probability snapshots."""

    __tablename__ = "respondent_predictions"
    __table_args__ = (
        UniqueConstraint("survey_id", "participant_id", "model_version", name="uq_prediction_snapshot"),
    )

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    model_version = Column(String, default="rule-v0.1", index=True)
    probability = Column(Float, nullable=False)
    confidence = Column(String, default="low")  # high / medium / low
    segment_label = Column(String, nullable=True)
    reasons_json = Column(JSON, nullable=True)
    risk_json = Column(JSON, nullable=True)
    features_json = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow() + timedelta(hours=6))

    survey = relationship("Survey", backref="respondent_predictions")
    participant = relationship("User", backref="respondent_predictions")


class SurveySegmentStats(Base):
    """Aggregated segment-level funnel stats shown to publishers."""

    __tablename__ = "survey_segment_stats"
    __table_args__ = (
        UniqueConstraint("survey_id", "segment_key", name="uq_survey_segment_stats"),
    )

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False, index=True)
    segment_key = Column(String, nullable=False, index=True)
    segment_label = Column(String, nullable=True)

    impressions = Column(Integer, default=0)
    starts = Column(Integer, default=0)
    completes = Column(Integer, default=0)
    completion_rate = Column(Float, default=0.0)
    avg_completion_minutes = Column(Float, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    survey = relationship("Survey", backref="segment_stats")


class UserActivityEvent(Base):
    """Generic event table for last-active and behavior signals."""

    __tablename__ = "user_activity_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    metadata_json = Column(JSON, nullable=True)

    user = relationship("User", backref="activity_events")
    survey = relationship("Survey", backref="activity_events")
