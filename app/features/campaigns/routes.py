"""Features / campaigns / routes."""

from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import hashlib
import hmac
from app.bootstrap.settings import BASE_URL
from app.database import get_db
from app.features.campaigns.messages import STUDY_RECOMMENDATION_CAMPAIGN_EVENT
from app.features.campaigns.messages import STUDY_RECOMMENDATION_SENT_EVENT
from app.features.campaigns.messages import STUDY_RECOMMENDATION_UNSUBSCRIBED_EVENT
from app.models import Survey
from app.models import User
from app.models import UserEvent


async def unsubscribe_study_recommendations(
    request: Request,
    user_id: int = Query(...),
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    from app.features.campaigns.messages import _study_email_unsubscribe_token

    expected = _study_email_unsubscribe_token(user_id)
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "Invalid unsubscribe link")
    existing = db.query(UserEvent.id).filter(
        UserEvent.user_id == user_id,
        UserEvent.event_name == STUDY_RECOMMENDATION_UNSUBSCRIBED_EVENT,
    ).first()
    if not existing:
        db.add(UserEvent(
            user_id=user_id,
            event_name=STUDY_RECOMMENDATION_UNSUBSCRIBED_EVENT,
            target_type="email_preference",
            target_id="study_recommendations",
            page_path=request.url.path,
            metadata_json={"source": "one_click_unsubscribe"},
        ))
        db.commit()
    return HTMLResponse("""
    <!doctype html><html lang="en"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Email preferences updated · Insighta</title>
    <body style="font-family:Arial,sans-serif;background:#f7f6f1;margin:0;padding:48px 20px;color:#1a1a18;">
      <main style="max-width:560px;margin:auto;background:white;border:1px solid #e0ddd3;border-radius:16px;padding:32px;">
        <h1 style="font-size:24px;margin-top:0;color:#184e77;">Email preferences updated</h1>
        <p style="line-height:1.6;">You will no longer receive study recommendation emails from Insighta.</p>
        <a href="/" style="color:#184e77;">Return to Insighta</a>
      </main>
    </body></html>
    """)


async def send_authorized_study_email_campaign(
    campaign_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    from app.features.auth.policy import _normalize_email
    from app.features.campaigns.messages import _study_recommendation_message
    from app.features.messaging.delivery import send_email_batch

    body = await request.json()
    provided_token = str(body.get("token") or "")
    campaign = db.query(UserEvent).filter(
        UserEvent.event_name == STUDY_RECOMMENDATION_CAMPAIGN_EVENT,
        UserEvent.target_type == "email_campaign",
        UserEvent.target_id == campaign_id,
    ).first()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    campaign_metadata = dict(campaign.metadata_json or {})
    expected_hash = str(campaign_metadata.get("token_sha256") or "")
    provided_hash = hashlib.sha256(provided_token.encode("utf-8")).hexdigest()
    if not expected_hash or not hmac.compare_digest(provided_hash, expected_hash):
        raise HTTPException(403, "Invalid campaign token")

    slugs = [str(value) for value in (campaign_metadata.get("study_slugs") or []) if value]
    surveys = db.query(Survey).filter(
        Survey.share_slug.in_(slugs),
        Survey.status == "published",
    ).all()
    survey_by_slug = {survey.share_slug: survey for survey in surveys}
    surveys = [survey_by_slug[slug] for slug in slugs if slug in survey_by_slug]
    if len(surveys) != len(slugs):
        raise HTTPException(409, "One or more authorized studies are not published")

    opted_out_ids = {
        row[0]
        for row in db.query(UserEvent.user_id).filter(
            UserEvent.event_name == STUDY_RECOMMENDATION_UNSUBSCRIBED_EVENT,
            UserEvent.user_id.isnot(None),
        ).all()
    }
    already_sent_ids = {
        row[0]
        for row in db.query(UserEvent.user_id).filter(
            UserEvent.event_name == STUDY_RECOMMENDATION_SENT_EVENT,
            UserEvent.target_type == "email_campaign",
            UserEvent.target_id == campaign_id,
            UserEvent.user_id.isnot(None),
        ).all()
    }
    recipients = []
    seen_emails = set()
    candidate_users = db.query(User).filter(User.email.isnot(None))
    # A campaign is authorized for the users who existed when that authorization
    # was created. New registrations belong to a future campaign and must not
    # silently expand a previously reviewed dry-run.
    if campaign.created_at:
        candidate_users = candidate_users.filter(User.created_at <= campaign.created_at)
    for user in candidate_users.order_by(User.id).all():
        normalized = _normalize_email(user.email or "")
        if (
            not normalized
            or "@" not in normalized
            or normalized in seen_emails
            or user.id in opted_out_ids
            or user.id in already_sent_ids
        ):
            continue
        seen_emails.add(normalized)
        user.email = normalized
        recipients.append(user)

    if bool(body.get("dry_run", True)):
        return JSONResponse({
            "dry_run": True,
            "campaign_id": campaign_id,
            "recipient_count": len(recipients),
            "opted_out_count": len(opted_out_ids),
            "already_sent_count": len(already_sent_ids),
            "subject": "Two paid Boston University research opportunities",
            "studies": [
                {
                    "title": survey.title.strip(),
                    "url": f"{BASE_URL.rstrip('/')}/r/{survey.share_slug}",
                    "reward": survey.admin_display_reward_amount
                    if survey.admin_display_reward_amount is not None
                    else survey.reward_amount,
                    "estimated_time": survey.estimated_time,
                }
                for survey in surveys
            ],
        })

    if body.get("confirm") != "SEND":
        raise HTTPException(400, "Set confirm to SEND")
    if campaign_metadata.get("status") != "ready":
        raise HTTPException(409, f"Campaign status is {campaign_metadata.get('status')}")
    campaign_metadata["status"] = "running"
    campaign_metadata["started_at"] = datetime.utcnow().isoformat()
    campaign.metadata_json = campaign_metadata
    db.commit()

    sent_count = 0
    failures = []
    for batch_index, start in enumerate(range(0, len(recipients), 100)):
        batch_users = recipients[start:start + 100]
        messages = [_study_recommendation_message(user, surveys) for user in batch_users]
        sent, resend_ids, error = send_email_batch(
            messages,
            idempotency_key=f"insighta/{campaign_id}/batch/{batch_index}",
        )
        if not sent:
            failures.append({
                "batch_index": batch_index,
                "recipient_count": len(batch_users),
                "error": error or "Unknown Resend error",
            })
            break
        for user, resend_id in zip(batch_users, resend_ids):
            db.add(UserEvent(
                user_id=user.id,
                event_name=STUDY_RECOMMENDATION_SENT_EVENT,
                target_type="email_campaign",
                target_id=campaign_id,
                metadata_json={"resend_email_id": resend_id, "study_slugs": slugs},
            ))
        db.commit()
        sent_count += len(batch_users)

    campaign_metadata = dict(campaign.metadata_json or {})
    campaign_metadata["status"] = "sent" if not failures else "partial_failure"
    campaign_metadata["finished_at"] = datetime.utcnow().isoformat()
    campaign_metadata["sent_count"] = sent_count
    campaign_metadata["failed_count"] = len(recipients) - sent_count
    campaign.metadata_json = campaign_metadata
    db.commit()
    return JSONResponse({
        "dry_run": False,
        "campaign_id": campaign_id,
        "recipient_count": len(recipients),
        "sent_count": sent_count,
        "failed_count": len(recipients) - sent_count,
        "failures": failures,
    })


__all__ = ['unsubscribe_study_recommendations', 'send_authorized_study_email_campaign']
