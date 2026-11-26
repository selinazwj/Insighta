"""Feedback persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    category = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(String, nullable=False)

    status = Column(String, default="pending")
    credit_amount = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    user = relationship("User", backref="feedbacks")
