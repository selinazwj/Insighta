"""Verification persistence models."""

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

class Verification(Base):
    __tablename__ = "verifications"
    __table_args__ = (
        UniqueConstraint("method", "evidence_ref", name="uq_verification_method_evidence"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    attribute = Column(String, nullable=False)   # 'is_student', 'is_physician'
    method = Column(String, nullable=False)       # 'edu_email', 'npi_registry'
    status = Column(String, default="pending")   # pending | verified | rejected
    trust_score = Column(Float, nullable=True)
    evidence_ref = Column(String, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    verified_by = Column(String, nullable=True)  # reviewer id or 'system'
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", backref="verifications")
