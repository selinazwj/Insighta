"""Trust persistence models."""

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

class RiskSignalEvent(Base):
    """Privacy-minimized security telemetry used by the human-risk engine."""

    __tablename__ = "risk_signal_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=True, index=True)
    event_type = Column(String, nullable=False, index=True)  # registration | start | completion
    network_ref = Column(String, nullable=True, index=True)  # HMAC, never the raw IP
    device_ref = Column(String, nullable=True, index=True)   # HMAC, never the raw fingerprint
    bot_challenge_passed = Column(Boolean, nullable=True)
    signal_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", backref="risk_signal_events")
    survey = relationship("Survey", backref="risk_signal_events")


class ParticipationDecision(Base):
    """An auditable snapshot of the checks used at each participation gate."""

    __tablename__ = "participation_decisions"

    id = Column(Integer, primary_key=True, index=True)
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=True, index=True)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=True, index=True)
    stage = Column(String, nullable=False, index=True)  # registration | start | completion | payout
    human_status = Column(String, nullable=False)
    human_risk_score = Column(Float, nullable=False, default=0.0)
    eligibility_status = Column(String, nullable=False, default="not_required")
    assurance_status = Column(String, nullable=False, default="passed")
    quality_status = Column(String, nullable=False, default="not_available")
    quality_score = Column(Float, nullable=True)
    outcome = Column(String, nullable=False, index=True)  # allow | review | block
    reasons = Column(JSON, nullable=True)
    decision_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    participant = relationship("User", backref="participation_decisions")
    survey = relationship("Survey", backref="participation_decisions")
    response = relationship("Response", backref="participation_decisions")
