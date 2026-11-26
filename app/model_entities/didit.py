"""Didit persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

class DiditSession(Base):
    """Tracks a Didit KYC session without storing document / biometric PII."""

    __tablename__ = "didit_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String, nullable=False, unique=True, index=True)
    workflow_id = Column(String, nullable=True)
    provider_status = Column(String, nullable=True)  # Approved | Declined | In Progress | ...
    status = Column(String, nullable=False, default="pending")  # pending | verified | rejected
    last_event_id = Column(String, nullable=True)
    provider_event_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="didit_sessions")


class DiditWebhookEvent(Base):
    """Idempotency log for Didit webhook deliveries (event_id is stable across retries)."""

    __tablename__ = "didit_webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String, nullable=False, unique=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    webhook_type = Column(String, nullable=True)
    provider_status = Column(String, nullable=True)
    processed_at = Column(DateTime, default=datetime.utcnow)
