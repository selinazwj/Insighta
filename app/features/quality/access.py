"""Features / quality / access."""

from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models import ResponseQualityCheck
from app.models import Survey


def _get_quality_check_for_publisher(db: Session, check_id: int, publisher_id: int):
    row = db.query(ResponseQualityCheck).filter(ResponseQualityCheck.id == check_id).first()
    if not row:
        raise HTTPException(404, "Quality check not found")
    survey = db.query(Survey).filter(
        Survey.id == row.survey_id,
        Survey.publisher_id == publisher_id,
    ).first()
    if not survey:
        raise HTTPException(403, "Not allowed")
    return row, survey


__all__ = ['_get_quality_check_for_publisher']
