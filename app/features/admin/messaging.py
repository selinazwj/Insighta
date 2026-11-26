"""Features / admin / messaging."""

from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.jobs.service import enqueue_admin_bulk_email_job
from app.models import User


async def admin_bulk_email(request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches
    from app.features.auth.policy import _normalize_email

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")):
        raise HTTPException(403, "Unauthorized")
    dry_run = bool(body.get("dry_run", True))
    subject = (body.get("subject") or "Complete your Insighta profile and payout setup").strip()
    message = (body.get("message") or """Quick reminder from Insighta: to make sure rewards can be matched, reviewed, and paid correctly, please complete your Insighta profile.

If you participate in paid studies, you will also need to connect Stripe before rewards can be paid out.

Profile: https://insightaco.org/profile
Stripe payouts: https://insightaco.org/connect/onboard""").strip()
    if not subject:
        raise HTTPException(400, "Email subject is required")
    if not message:
        raise HTTPException(400, "Email message is required")
    if len(subject) > 180:
        raise HTTPException(400, "Email subject is too long")
    if len(message) > 5000:
        raise HTTPException(400, "Email message is too long")
    users = db.query(User).filter(User.email.isnot(None)).order_by(User.created_at.desc()).all()
    recipients: list[User] = []
    seen_emails = set()
    for user in users:
        normalized = _normalize_email(user.email or "")
        if not normalized or "@" not in normalized or normalized in seen_emails:
            continue
        seen_emails.add(normalized)
        recipients.append(user)
    if dry_run:
        return JSONResponse({
            "dry_run": True,
            "recipient_count": len(recipients),
        })

    campaign_key = datetime.utcnow().strftime("%Y%m%d")
    queued_count = 0
    for user in recipients:
        enqueue_admin_bulk_email_job(
            db,
            user_id=user.id,
            email=user.email or "",
            subject=subject,
            message=message,
            campaign_key=campaign_key,
        )
        queued_count += 1
    db.commit()
    return JSONResponse({
        "dry_run": False,
        "recipient_count": len(recipients),
        "queued_count": queued_count,
        "sent_count": 0,
        "failed_count": 0,
        "campaign_key": campaign_key,
        "subject": subject,
    })


__all__ = ['admin_bulk_email']
