"""Activity persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import Text
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

class UserEvent(Base):
    __tablename__ = "user_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    anonymous_id = Column(String, nullable=True, index=True)
    event_name = Column(String, nullable=False, index=True)
    target_type = Column(String, nullable=True, index=True)
    target_id = Column(String, nullable=True, index=True)
    page_path = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    user_agent = Column(Text, nullable=True)
    client_ip = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", backref="events")


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String, nullable=False, index=True)
    status = Column(String, default="pending", nullable=False, index=True)
    payload_json = Column(JSON, nullable=True)
    dedupe_key = Column(String, unique=True, index=True, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    run_after = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    locked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
