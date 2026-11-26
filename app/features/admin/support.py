"""Features / admin / support."""

from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.features.support.routes import SUPPORT_AVAILABLE_HOURS
from app.models import SupportMessage
from app.models import SupportThread


async def admin_support_threads(admin_key: str = Query(None), db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.support.routes import _support_thread_payload

    if not _admin_key_matches(admin_key): raise HTTPException(403, "Unauthorized")
    threads = db.query(SupportThread).order_by(SupportThread.last_message_at.desc()).all()
    return JSONResponse([_support_thread_payload(thread, db) for thread in threads])


async def admin_support_messages(thread_id: int, admin_key: str = Query(None), db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.support.routes import _support_message_payload
    from app.features.support.routes import _support_thread_payload

    if not _admin_key_matches(admin_key): raise HTTPException(403, "Unauthorized")
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    if not thread:
        raise HTTPException(404, "Support thread not found")
    messages = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id
    ).order_by(SupportMessage.created_at.asc()).all()
    now = datetime.utcnow()
    for msg in messages:
        if msg.sender_type == "user" and msg.read_at is None:
            msg.read_at = now
    db.commit()
    return JSONResponse({
        "thread": _support_thread_payload(thread, db),
        "messages": [_support_message_payload(m) for m in messages],
        "available_hours": SUPPORT_AVAILABLE_HOURS,
    })


async def admin_send_support_message(thread_id: int, request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.support.routes import _support_message_payload
    from app.features.support.routes import _support_thread_payload

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")): raise HTTPException(403, "Unauthorized")
    text_body = (body.get("body") or "").strip()
    if not text_body:
        raise HTTPException(400, "Message cannot be empty")
    if len(text_body) > 2000:
        raise HTTPException(400, "Message is too long")
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    if not thread:
        raise HTTPException(404, "Support thread not found")
    msg = SupportMessage(thread_id=thread.id, sender_type="admin", body=text_body)
    thread.status = "open"
    thread.last_message_at = datetime.utcnow()
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return JSONResponse({"message": _support_message_payload(msg), "thread": _support_thread_payload(thread, db)})


async def admin_update_support_status(thread_id: int, request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.support.routes import _support_thread_payload

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")): raise HTTPException(403, "Unauthorized")
    status = (body.get("status") or "").strip().lower()
    if status not in {"open", "closed"}:
        raise HTTPException(400, "Unsupported status")
    thread = db.query(SupportThread).filter(SupportThread.id == thread_id).first()
    if not thread:
        raise HTTPException(404, "Support thread not found")
    thread.status = status
    thread.updated_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"thread": _support_thread_payload(thread, db)})


__all__ = ['admin_support_threads', 'admin_support_messages', 'admin_send_support_message', 'admin_update_support_status']
