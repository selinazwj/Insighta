"""Responses persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import func
from sqlalchemy.orm import relationship
from .base import Base

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
    review_due_at = Column(DateTime, nullable=True)
    payout_reviewed_at = Column(DateTime, nullable=True)

    client_ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    device_fingerprint = Column(String, nullable=True)
    booking_slot = Column(String, nullable=True)
    start_followup_scheduled_at = Column(DateTime(timezone=True), nullable=True)
    start_followup_sent_at = Column(DateTime(timezone=True), nullable=True)

    survey = relationship("Survey", back_populates="responses")
    participant = relationship("User", back_populates="responses")
    answers = relationship("Answer", back_populates="response")
