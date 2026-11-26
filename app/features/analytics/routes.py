"""Features / analytics / routes."""

from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import json
from app.database import get_db
from app.models import User
from app.models import UserEvent


async def record_client_event(
    request: Request,
    user_id: str = Cookie(None),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event

    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {}

    current_user = None
    if user_id:
        try:
            current_user = db.query(User).filter(User.id == int(user_id)).first()
        except Exception:
            current_user = None

    event_name = (body.get("event_name") or "").strip().lower()
    if not event_name:
        raise HTTPException(400, "event_name is required")
    anonymous_id = (body.get("anonymous_id") or "").strip()
    if current_user and anonymous_id:
        db.query(UserEvent).filter(
            UserEvent.anonymous_id == anonymous_id[:120],
            UserEvent.user_id.is_(None),
        ).update({"user_id": current_user.id}, synchronize_session=False)
    _record_user_event(
        db,
        request,
        event_name,
        user=current_user,
        anonymous_id=anonymous_id,
        target_type=body.get("target_type"),
        target_id=body.get("target_id"),
        page_path=body.get("page_path"),
        metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
    )
    db.commit()
    return JSONResponse({"success": True})


__all__ = ['record_client_event']
