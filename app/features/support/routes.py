"""Features / support / routes."""

from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import Feedback
from app.models import SupportMessage
from app.models import SupportThread
from app.models import User


def feedback_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("feedback.html", {"request": request, "current_user": current_user})


async def submit_feedback(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    form = await request.form()
    db.add(Feedback(user_id=current_user.id, category=form.get("category", "general"), title=form.get("title", ""), content=form.get("content", ""), status="pending"))
    db.commit()
    return RedirectResponse("/feedback?success=1", status_code=303)


SUPPORT_AVAILABLE_HOURS = "10am-5pm"


def _support_thread_payload(thread: SupportThread, db: Session):
    user = db.query(User).filter(User.id == thread.user_id).first()
    last_message = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id
    ).order_by(SupportMessage.created_at.desc()).first()
    unread_user_messages = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id,
        SupportMessage.sender_type == "user",
        SupportMessage.read_at.is_(None),
    ).count()
    return {
        "id": thread.id,
        "status": thread.status,
        "user_id": thread.user_id,
        "user_email": user.email if user else "unknown",
        "user_name": user.username if user else None,
        "last_message": last_message.body if last_message else "",
        "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "unread_user_messages": unread_user_messages,
    }


def _support_message_payload(message: SupportMessage):
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "sender_type": message.sender_type,
        "sender_id": message.sender_id,
        "body": message.body,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _get_or_create_support_thread(db: Session, user_id: int):
    thread = db.query(SupportThread).filter(
        SupportThread.user_id == user_id,
        SupportThread.status == "open",
    ).order_by(SupportThread.updated_at.desc()).first()
    if thread:
        return thread
    thread = SupportThread(user_id=user_id, status="open", last_message_at=datetime.utcnow())
    db.add(thread)
    db.flush()
    db.add(SupportMessage(
        thread_id=thread.id,
        sender_type="system",
        body=f"Support hours are {SUPPORT_AVAILABLE_HOURS}. Leave a message and our team will reply here.",
    ))
    db.commit()
    db.refresh(thread)
    return thread


def support_availability():
    return JSONResponse({
        "available_hours": SUPPORT_AVAILABLE_HOURS,
        "label": f"Human support available {SUPPORT_AVAILABLE_HOURS}",
    })


def get_support_thread(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    thread = _get_or_create_support_thread(db, current_user.id)
    messages = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id
    ).order_by(SupportMessage.created_at.asc()).all()
    return JSONResponse({
        "thread": _support_thread_payload(thread, db),
        "messages": [_support_message_payload(m) for m in messages],
        "available_hours": SUPPORT_AVAILABLE_HOURS,
    })


def get_support_messages(
    thread_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    thread = db.query(SupportThread).filter(
        SupportThread.id == thread_id,
        SupportThread.user_id == current_user.id,
    ).first()
    if not thread:
        raise HTTPException(404, "Support thread not found")
    messages = db.query(SupportMessage).filter(
        SupportMessage.thread_id == thread.id
    ).order_by(SupportMessage.created_at.asc()).all()
    return JSONResponse([_support_message_payload(m) for m in messages])


async def send_support_message(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.features.messaging.account_messages import send_support_alert_email

    body = await request.json()
    text_body = (body.get("body") or "").strip()
    if not text_body:
        raise HTTPException(400, "Message cannot be empty")
    if len(text_body) > 2000:
        raise HTTPException(400, "Message is too long")

    thread_id = body.get("thread_id")
    thread = None
    if thread_id:
        thread = db.query(SupportThread).filter(
            SupportThread.id == int(thread_id),
            SupportThread.user_id == current_user.id,
        ).first()
    if not thread:
        thread = _get_or_create_support_thread(db, current_user.id)

    msg = SupportMessage(
        thread_id=thread.id,
        sender_type="user",
        sender_id=current_user.id,
        body=text_body,
    )
    thread.status = "open"
    thread.last_message_at = datetime.utcnow()
    db.add(msg)
    db.commit()
    db.refresh(msg)
    send_support_alert_email(current_user, thread, msg)
    return JSONResponse({
        "thread": _support_thread_payload(thread, db),
        "message": _support_message_payload(msg),
    })


__all__ = ['feedback_page', 'submit_feedback', 'SUPPORT_AVAILABLE_HOURS', '_support_thread_payload', '_support_message_payload', '_get_or_create_support_thread', 'support_availability', 'get_support_thread', 'get_support_messages', 'send_support_message']
