"""Features / studies / sharing."""

from fastapi import HTTPException
from fastapi import Request
from sqlalchemy.orm import Session
import secrets
from app.models import Survey


def _absolute_url(request: Request, path: str) -> str:
    base = str(request.base_url).rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _new_share_slug() -> str:
    return secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]


def _ensure_survey_share_slug(db: Session, survey: Survey, commit: bool = False) -> str:
    if getattr(survey, "share_slug", None):
        return survey.share_slug
    for _ in range(12):
        slug = _new_share_slug()
        if not db.query(Survey).filter(Survey.share_slug == slug).first():
            survey.share_slug = slug
            if commit:
                db.commit()
                db.refresh(survey)
            return slug
    raise HTTPException(500, "Could not create share link")


def _survey_share_path(db: Session, survey: Survey, commit: bool = False) -> str:
    return f"/r/{_ensure_survey_share_slug(db, survey, commit=commit)}"


def _survey_share_url(request: Request, db: Session, survey: Survey, commit: bool = False) -> str:
    return _absolute_url(request, _survey_share_path(db, survey, commit=commit))


__all__ = ['_absolute_url', '_new_share_slug', '_ensure_survey_share_slug', '_survey_share_path', '_survey_share_url']
