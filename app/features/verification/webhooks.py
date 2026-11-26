"""Features / verification / webhooks."""

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import json
from app.database import get_db
from app.jobs.service import enqueue_didit_webhook_process_job
from app.verification.didit import is_didit_test_webhook
from app.verification.didit import parse_vendor_user_id
from app.verification.didit import verify_didit_webhook
from app.verification.didit import webhook_secrets as didit_webhook_secrets
from app.verification.didit_processor import DiditWebhookDuplicate
from app.verification.didit_processor import DiditWebhookError
from app.verification.didit_processor import DiditWebhookIgnored
from app.verification.didit_processor import process_didit_webhook_body


async def didit_webhook(request: Request, db: Session = Depends(get_db)):
    """Didit status.updated callback: verify signature, upsert session, update evidence."""
    secrets = didit_webhook_secrets()
    if not secrets:
        raise HTTPException(503, "Didit webhook is not configured")

    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid JSON body")

    if not verify_didit_webhook(body, request.headers, secrets=secrets):
        raise HTTPException(401, "Invalid Didit signature")

    # Didit console "Test Webhook" uses synthetic vendor_data; ack after signature check.
    if is_didit_test_webhook(request.headers):
        return JSONResponse({"status": "ok", "test": True})

    event_id = str(body.get("event_id") or "").strip()
    try:
        result = process_didit_webhook_body(db, body)
        db.commit()
        return JSONResponse({"status": "ok", **result})
    except DiditWebhookDuplicate:
        return JSONResponse({"status": "ok", "duplicate": True})
    except DiditWebhookIgnored as exc:
        db.commit()
        return JSONResponse({"status": "ok", "ignored": True, "reason": exc.reason})
    except DiditWebhookError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message)
    except Exception:
        db.rollback()
        if event_id:
            retry_body = {key: body[key] for key in (
                "event_id", "webhook_type", "session_id", "status",
                "workflow_id", "timestamp", "created_at",
            ) if key in body}
            retry_body["vendor_data"] = parse_vendor_user_id(body.get("vendor_data"), body.get("metadata"))
            enqueue_didit_webhook_process_job(
                db,
                payload={"body": retry_body, "event_id": event_id},
                event_id=event_id,
            )
            db.commit()
            return JSONResponse({"status": "ok", "queued": True})
        raise HTTPException(500, "Failed to process Didit webhook")


__all__ = ['didit_webhook']
