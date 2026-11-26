"""Calendar persistence models."""

from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from .base import Base

class GoogleCalendarConnection(Base):
    __tablename__ = "google_calendar_connections"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    google_subject = Column(String, nullable=False)
    google_email = Column(String, nullable=False)
    refresh_token_encrypted = Column(Text, nullable=False)
    access_token_encrypted = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    needs_reconnect = Column(Boolean, nullable=False, default=False)


class GoogleCalendarEvent(Base):
    __tablename__ = "google_calendar_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=False)
    session_index = Column(Integer, nullable=False)
    event_id = Column(String, nullable=False)
    google_subject = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "response_id", "session_index"),)
