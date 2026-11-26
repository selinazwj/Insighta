"""Support persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

class SupportThread(Base):
    __tablename__ = "support_threads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String, default="open")  # open / closed
    last_message_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", backref="support_threads")
    messages = relationship("SupportMessage", back_populates="thread")


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("support_threads.id"), nullable=False, index=True)
    sender_type = Column(String, nullable=False)  # user / admin / system
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)

    thread = relationship("SupportThread", back_populates="messages")
