"""Notifications persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from datetime import datetime
from .base import Base

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
