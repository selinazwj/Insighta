"""Versioned manual recruitment offers; paid snapshots remain immutable."""
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from app.models import Base


class NicheRequest(Base):
    __tablename__ = 'niche_requests'
    id = Column(Integer, primary_key=True)
    survey_id = Column(Integer, ForeignKey('surveys.id'), nullable=False, unique=True, index=True)
    requirements = Column(Text, nullable=False)
    status = Column(String, nullable=False, default='requested')
    current_quote_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class NicheQuote(Base):
    __tablename__ = 'niche_quotes'
    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey('niche_requests.id'), nullable=False, index=True)
    snapshot = Column(JSON, nullable=False)
    notes = Column(Text, nullable=False, default='')
    status = Column(String, nullable=False, default='offered')
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    checkout_expires_at = Column(DateTime, nullable=True)
    refund_destination = Column(String, nullable=True)
    stripe_session_id = Column(String, nullable=True, unique=True)
    checkout_url = Column(Text, nullable=True)
