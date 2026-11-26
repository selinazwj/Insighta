"""Quality persistence models."""

from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import Text
from datetime import datetime
from .base import Base

class ResponseQualityCheck(Base):
    __tablename__ = "response_quality_checks"

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=True, index=True)
    source_type = Column(String, default="builtin")  # builtin | excel
    source_ref = Column(String, nullable=True)       # e.g. filename:row_index

    quality_score = Column(Float, nullable=False, default=100.0)
    quality_label = Column(String, nullable=False, default="high")
    fraud_risk = Column(Boolean, default=False)

    rule_penalty = Column(Float, default=0.0)
    anomaly_score = Column(Float, default=0.0)
    semantic_risk = Column(Float, default=0.0)

    triggered_rules = Column(JSON, nullable=True)
    reasons = Column(JSON, nullable=True)
    llm_result_json = Column(JSON, nullable=True)

    review_status = Column(String, default="pending")  # pending / approved / rejected / needs_review
    reviewer_label = Column(String, nullable=True)

    raw_response_json = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class QualityBlacklist(Base):
    __tablename__ = "quality_blacklist"

    id = Column(Integer, primary_key=True, index=True)
    block_type = Column(String, nullable=False)   # ip | user | device
    block_value = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
