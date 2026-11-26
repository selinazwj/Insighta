"""Features / billing / routes."""

from app import niche
from app import wallet as researcher_wallet
from app import withdrawals
from datetime import datetime
from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import asyncio
import stripe
from app.bootstrap.runtime import templates
from app.bootstrap.settings import BASE_URL
from app.bootstrap.settings import STRIPE_WEBHOOK_SECRET
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import StudySettlement
from app.models import Survey
from app.models import User
from app.settlements import lock_study
from app.stripe_data import plain_stripe_data
from app.wallet_models import WalletRefundPart


def _recover_study_checkout(survey, checkout):
    """Resolve a paid session when the Checkout create response was lost."""
    session_id = checkout.get("id")
    if not stripe.api_key or not isinstance(session_id, str) or not session_id.startswith("cs_"):
        raise HTTPException(503, "Study payment reconciliation is pending. Retry this webhook.")
    try:
        confirmed = plain_stripe_data(stripe.checkout.Session.retrieve(session_id))
    except Exception:
        raise HTTPException(503, "Study payment reconciliation is pending. Retry this webhook.")
    snapshot = survey.pricing_snapshot
    metadata = confirmed.get("metadata") if isinstance(confirmed, dict) else None
    intent = confirmed.get("payment_intent") if isinstance(confirmed, dict) else None
    if (not isinstance(confirmed, dict) or not isinstance(metadata, dict)
            or confirmed.get("id") != session_id or confirmed.get("mode") != "payment"
            or confirmed.get("payment_status") != "paid"
            or confirmed.get("amount_total") != snapshot["total_cents"]
            or confirmed.get("currency") != snapshot["currency"]
            or str(metadata.get("survey_id")) != str(survey.id)
            or str(metadata.get("publisher_id")) != str(survey.publisher_id)
            or not isinstance(intent, str) or not intent.startswith("pi_")):
        raise HTTPException(400, "Study funding does not match the saved order.")
    survey.stripe_payment_intent_id = session_id
    return confirmed


def payment_success(
    request: Request,
    survey_id: int = Query(None),
    user_id: str = Cookie(None),
    db: Session = Depends(get_db)
):
    current_user = None
    if user_id:
        try:
            current_user = db.query(User).filter(User.id == int(user_id)).first()
        except:
            pass
    survey = db.query(Survey).filter(Survey.id == survey_id).first() if survey_id else None

    if survey and (not current_user or survey.publisher_id != current_user.id):
        raise HTTPException(404, "Study not found")
    # Publication is driven only by a verified Stripe webhook.
    return templates.TemplateResponse("payment_success.html", {
        "request": request, "survey": survey, "current_user": current_user,
    })


async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    from app.features.analytics.events import _record_user_event

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = plain_stripe_data(stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET))
    except Exception as e:
        raise HTTPException(400, str(e))

    if (not isinstance(event, dict) or not isinstance(event.get("type"), str)
            or not isinstance(event.get("data"), dict)
            or not isinstance(event["data"].get("object"), dict)):
        raise HTTPException(400, "Send a valid Stripe event.")
    event_object = event["data"]["object"]
    if event["type"] in {"checkout.session.completed", "refund.created", "refund.updated", "refund.failed"}:
        if event_object.get("metadata") is None:
            event_object["metadata"] = {}
        elif not isinstance(event_object["metadata"], dict):
            raise HTTPException(400, "Send valid Stripe event metadata.")

    if event["type"] == "checkout.session.completed":
        # Validate identifiers before niche/top-up dispatch performs DB lookups.
        for field in ("survey_id", "wallet_topup_id"):
            value = event_object.get("metadata", {}).get(field)
            if value is None or value == "":
                continue
            try:
                if isinstance(value, bool) or not 0 < int(value) <= 2**63 - 1:
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(400, "Send valid payment order identifiers.")
        if niche.fulfill_checkout(db, event["data"]["object"]):
            db.commit()
            return JSONResponse({"status": "ok"})
        if researcher_wallet.fulfill_topup(db, event["data"]["object"]):
            db.commit()
            return JSONResponse({"status": "ok"})
        survey_id = event["data"]["object"].get("metadata", {}).get("survey_id")
        if survey_id:
            try:
                survey_id = int(survey_id)
                if not 0 < survey_id <= 2**63 - 1:
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                raise HTTPException(400, "Send a valid study identifier.")
            survey = lock_study(db, survey_id)
            checkout = event["data"]["object"]
            if (survey and survey.funding_source != "wallet" and survey.payment_status != "paid"
                    and survey.pricing_snapshot and not survey.stripe_payment_intent_id
                    and checkout.get("payment_status") == "paid"):
                checkout = _recover_study_checkout(survey, checkout)
            if (survey and survey.funding_source != "wallet" and survey.payment_status != "paid" and checkout.get("payment_status") == "paid"
                    and (not survey.pricing_snapshot or (
                        checkout.get("id") == survey.stripe_payment_intent_id
                        and checkout.get("amount_total") == survey.pricing_snapshot["total_cents"]
                        and checkout.get("currency") == survey.pricing_snapshot["currency"]))):
                was_published = survey.status == "published"
                payment_intent = checkout.get("payment_intent")
                if isinstance(payment_intent, str) and payment_intent.startswith("pi_"):
                    survey.stripe_funding_payment_intent_id = payment_intent
                survey.payment_status = "paid"
                survey.status = "published"
                survey.published_at = datetime.utcnow()
                if not was_published:
                    publisher = db.query(User).filter(User.id == survey.publisher_id).first()
                    _record_user_event(
                        db,
                        request,
                        "study_published",
                        user=publisher,
                        target_type="survey",
                        target_id=survey.id,
                        metadata={"study_title": survey.title, "source": "Stripe webhook", "user_role": "researcher"},
                    )
                researcher_wallet.issue_study_invoice(db, survey)
                db.commit()
    elif event["type"] in {"refund.created", "refund.updated", "refund.failed"}:
        from app.settlements import _apply_refund
        refund = event["data"]["object"]
        part_marker = str(refund.get("metadata", {}).get("insighta_wallet_part_id", ""))
        if part_marker.isdigit():
            part = db.get(WalletRefundPart, int(part_marker))
            if part:
                row = db.get(StudySettlement, part.settlement_id)
                lock_study(db, row.survey_id)
                if not part.stripe_refund_id or part.stripe_refund_id == refund.get("id"):
                    try:
                        researcher_wallet.apply_part(part, stripe.Refund.retrieve(refund["id"]))
                        researcher_wallet.refresh_parts_status(db, row)
                    except Exception:
                        raise HTTPException(503, "Refund allocation could not be reconciled. Retry this webhook.")
                    db.commit()
            return JSONResponse({"status":"ok"})
        marker = str(refund.get("metadata", {}).get("insighta_settlement_id", ""))
        if marker.isdigit():
            existing = db.get(StudySettlement, int(marker))
            if existing:
                lock_study(db, existing.survey_id)
                row = db.query(StudySettlement).filter_by(id=existing.id).populate_existing().with_for_update().one()
                if row.frozen_at and row.payment_intent_id and (not row.stripe_refund_id or row.stripe_refund_id == refund.get("id")):
                    # Re-fetch authoritative status so late events cannot undo a newer result.
                    try:
                        _apply_refund(row, stripe.Refund.retrieve(refund["id"]))
                    except Exception:
                        raise HTTPException(503, "Refund status could not be reconciled. Retry this webhook.")
                    db.commit()
    elif event["type"] in {"charge.refunded", "charge.dispute.created"}:
        researcher_wallet.freeze_wallet_for_payment_event(db, event)
        db.commit()
    elif event["type"] == "account.updated":
        account = event["data"]["object"]
        user = db.query(User).filter(User.stripe_account_id == account.get("id")).first()
        if user:
            ready = (account.get('details_submitted') and account.get('payouts_enabled')
                     and (account.get('capabilities') or {}).get('transfers') == 'active')
            user.stripe_onboarding_complete = 'true' if ready else 'false'
            db.commit()

    return JSONResponse({"status": "ok"})


def connect_onboard(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user = db.query(User).filter_by(id=current_user.id).populate_existing().with_for_update().one()
    if not stripe.api_key:
        raise HTTPException(503, 'Stripe onboarding is unavailable.')
    if not getattr(current_user, 'stripe_account_id', None):
        account = stripe.Account.create(
            type="express",
            email=current_user.email,
            business_type="individual",
            individual={"email": current_user.email},
            capabilities={"transfers": {"requested": True}},
            idempotency_key=f'connect-onboarding:{current_user.id}:v1'
        )
        current_user.stripe_account_id = account.id; db.commit()
    account_link = stripe.AccountLink.create(
        account=current_user.stripe_account_id,
        refresh_url=BASE_URL.rstrip('/')+"/connect/onboard",
        return_url=BASE_URL.rstrip('/')+"/connect/complete",
        type="account_onboarding",
    )
    return RedirectResponse(account_link.url)


def connect_complete(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if getattr(current_user, 'stripe_account_id', None):
        account = stripe.Account.retrieve(current_user.stripe_account_id)
        account = plain_stripe_data(account)
        if (account.get('details_submitted') and account.get('payouts_enabled')
                and (account.get('capabilities') or {}).get('transfers') == 'active'):
            current_user.stripe_onboarding_complete = "true"
        else:
            current_user.stripe_onboarding_complete = "false"
        db.commit()
    return RedirectResponse("/dashboard")


async def withdraw(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return await asyncio.to_thread(withdrawals.withdraw, db, current_user.id)


def withdrawal_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(withdrawals.ParticipantWithdrawal).filter_by(user_id=current_user.id).order_by(
        withdrawals.ParticipantWithdrawal.id.desc()).first()
    if not row:
        return {'withdrawal':None}
    return {'withdrawal':{'id':row.id, 'amount_cents':row.amount_cents, 'currency':'usd',
            'status':row.status, 'created_at':row.created_at.isoformat()+'Z'}}


__all__ = ['payment_success', 'stripe_webhook', 'connect_onboard', 'connect_complete', 'withdraw', 'withdrawal_status']
