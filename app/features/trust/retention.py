"""Features / trust / retention."""

from datetime import datetime
from datetime import timedelta
from sqlalchemy.orm import Session
import os
from app.database import SessionLocal
from app.models import ParticipationDecision
from app.models import RiskSignalEvent


def _trust_retention_days() -> tuple[int, int]:
    try:
        signal_days = min(365, max(7, int(
            os.environ.get("RISK_SIGNAL_RETENTION_DAYS", "90")
        )))
        decision_days = min(2555, max(90, int(
            os.environ.get("TRUST_DECISION_RETENTION_DAYS", "730")
        )))
    except ValueError:
        signal_days, decision_days = 90, 730
    return signal_days, decision_days


def _cleanup_expired_trust_data_in_session(db: Session) -> dict[str, int]:
    """Apply explicit retention limits to privacy-sensitive security telemetry."""
    signal_days, decision_days = _trust_retention_days()
    now = datetime.utcnow()
    deleted_signals = db.query(RiskSignalEvent).filter(
        RiskSignalEvent.created_at < now - timedelta(days=signal_days),
    ).delete(synchronize_session=False)
    deleted_decisions = db.query(ParticipationDecision).filter(
        ParticipationDecision.created_at < now - timedelta(days=decision_days),
    ).delete(synchronize_session=False)
    return {
        "risk_signal_events_deleted": deleted_signals,
        "participation_decisions_deleted": deleted_decisions,
    }


def _cleanup_expired_trust_data() -> dict[str, int]:
    db = SessionLocal()
    try:
        result = _cleanup_expired_trust_data_in_session(db)
        db.commit()
        return result
    finally:
        db.close()


__all__ = ['_trust_retention_days', '_cleanup_expired_trust_data_in_session', '_cleanup_expired_trust_data']
