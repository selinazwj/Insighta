"""Snapshot-based closeout and durable, reconcilable Stripe original-card refunds."""
from datetime import datetime, timedelta, timezone

import stripe
from fastapi import HTTPException
from sqlalchemy import or_

from app.models import Survey, Response, User, Notification, ResponseQualityCheck, ParticipationDecision, StudySettlement

REVIEW_DAYS = 7


def utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


def review_deadline(response):
    return utc_naive(response.review_due_at) or (
        utc_naive(response.completed_at) + timedelta(days=REVIEW_DAYS) if response.completed_at else None)


def lock_study(db, survey_id):
    return db.query(Survey).filter(Survey.id == survey_id).populate_existing().with_for_update().first()


def assert_review_mutable(db, response, *, action="approve", automated=False, now=None):
    study = lock_study(db, response.survey_id)
    if not study or not study.pricing_snapshot or study.payment_status != "paid":
        return study
    db.flush()
    db.refresh(response)
    settlement = db.query(StudySettlement).filter_by(survey_id=study.id).first()
    if settlement and settlement.frozen_at:
        raise HTTPException(409, "This study's settlement is locked; payout decisions can no longer change.")
    due = review_deadline(response)
    if not automated and action != "approve" and due and (now or datetime.utcnow()) >= due:
        raise HTTPException(409, "The seven-day response review window has ended.")
    if response.payout_status == "paid" and action != "approve":
        raise HTTPException(409, "Paid participant rewards cannot be revoked.")
    if action == "approve":
        if response.status != "completed":
            raise HTTPException(409, "Only completed participation can be approved.")
        if response.payout_status not in {"approved", "paid"}:
            funded = db.query(Response).filter(Response.survey_id == study.id,
                Response.status == "completed", Response.payout_status.in_(["approved", "paid"])).all()
            if any(row.participant_id == response.participant_id for row in funded):
                raise HTTPException(409, "This participant already has a funded completion.")
            if len({row.participant_id for row in funded}) >= study.pricing_snapshot["target_responses"]:
                raise HTTPException(409, "The purchased participant limit has been reached.")
    return study


def assert_study_open(db, study):
    if not study.pricing_snapshot:
        return
    current = lock_study(db, study.id)
    if current.status != "published" or (current.deadline_at and utc_naive(current.deadline_at) <= datetime.utcnow()):
        raise HTTPException(409, "This study is closed or its completion deadline has passed.")


def refund_cents(snapshot, qualified_count, reason="completed"):
    count = snapshot["target_responses"]
    unused = count - min(max(int(qualified_count), 0), count)
    base = unused * snapshot["reward_per_person_cents"]
    if reason != "researcher_cancelled":
        base += unused * snapshot["recruitment_fee_per_person_cents"]
    refund = base + (base * snapshot["urgency_bps"] + 5000) // 10000
    return min(refund, snapshot["total_cents"])


def ensure_settlement(db, study):
    if not study.pricing_snapshot or study.payment_status != "paid" or study.status != "closed":
        return None
    row = db.query(StudySettlement).filter_by(survey_id=study.id).first()
    if not row:
        row = StudySettlement(survey_id=study.id, status="waiting_review",
            close_reason=study.close_reason or "completed", pricing_snapshot=dict(study.pricing_snapshot),
            currency=study.pricing_snapshot["currency"])
        db.add(row)
        db.flush()
    return row


def _eligible_for_auto_approval(db, study, response, verification_satisfies):
    person = db.get(User, response.participant_id)
    if not person or not verification_satisfies(person, study, db):
        return False
    quality = db.query(ResponseQualityCheck).filter_by(response_id=response.id).order_by(ResponseQualityCheck.id.desc()).first()
    if quality and (quality.fraud_risk or quality.quality_label in {"low", "fraud_risk", "needs_review"}
                    or quality.review_status in {"rejected", "needs_review"}):
        return False
    decision = db.query(ParticipationDecision).filter_by(response_id=response.id).order_by(ParticipationDecision.created_at.desc()).first()
    return not decision or decision.outcome == "allow"


def scan_settlements(db, verification_satisfies, *, now=None):
    from app.payouts import release_response_payout
    now = now or datetime.utcnow()
    # Scan only funded quotes. Legacy orders and admin demos retain their old behavior.
    ids = db.query(Survey.id).filter(Survey.pricing_snapshot.isnot(None), Survey.payment_status == "paid",
        Survey.status.in_(["published", "closed"]),
        or_(Survey.status == "published", ~Survey.id.in_(
            db.query(StudySettlement.survey_id).filter(StudySettlement.frozen_at.isnot(None)))))
    # A cursorless LIMIT would starve older orders. Scan all eligible IDs; each
    # individual study is serialized by its row lock.
    for (survey_id,) in ids.order_by(Survey.id).all():
        study = lock_study(db, survey_id)
        if not study or not study.pricing_snapshot or study.payment_status != "paid":
            db.commit()
            continue
        if study.deadline_at and utc_naive(study.deadline_at) <= now and study.status == "published":
            study.status, study.closed_at, study.close_reason = "closed", now, "deadline"
        settlement = ensure_settlement(db, study)
        if settlement and settlement.frozen_at:
            db.commit()
            continue
        responses = db.query(Response).filter(Response.survey_id == study.id, Response.status == "completed").order_by(Response.id).all()
        waiting, held = False, False
        for response in responses:
            if response.payout_status != "pending_review":
                continue
            due = review_deadline(response)
            if not due or due > now:
                waiting = True
                continue
            if not _eligible_for_auto_approval(db, study, response, verification_satisfies):
                held = True
                continue
            try:
                release_response_payout(db, response, automated=True, now=now)
            except HTTPException as exc:
                if "limit" not in exc.detail and "already has" not in exc.detail:
                    raise
                response.payout_status = "over_capacity"
            db.query(Notification).filter_by(survey_id=study.id, participant_id=response.participant_id,
                status="pending").update({"status": "accepted" if response.payout_status == "approved" else "rejected"})
        if settlement:
            if waiting or held:
                settlement.status = "waiting_review" if waiting else "waiting_verification"
            else:
                funded = [r for r in responses if r.payout_status in {"approved", "paid"}]
                distinct = {}
                for response in funded:
                    distinct.setdefault(response.participant_id, response.id)
                settlement.qualified_response_ids = list(distinct.values())[:study.pricing_snapshot["target_responses"]]
                settlement.qualified_count = len(settlement.qualified_response_ids)
                settlement.refund_amount_cents = refund_cents(settlement.pricing_snapshot,
                    settlement.qualified_count, settlement.close_reason)
                settlement.frozen_at = now
                settlement.status = "ready" if settlement.refund_amount_cents else "no_refund"
                settlement.next_attempt_at = now
        db.commit()


def _funding_intent(study, settlement):
    intent = study.stripe_funding_payment_intent_id
    if not intent:
        # Phase-one orders stored a Checkout Session ID in this legacy field.
        session = stripe.checkout.Session.retrieve(study.stripe_payment_intent_id)
        q = settlement.pricing_snapshot
        if (session.get("payment_status") != "paid" or session.get("amount_total") != q["total_cents"]
                or session.get("currency") != settlement.currency
                or str(session.get("metadata", {}).get("survey_id")) != str(study.id)):
            raise RuntimeError("Original funding could not be verified; manual reconciliation is required.")
        intent = session.get("payment_intent")
    if not isinstance(intent, str) or not intent.startswith("pi_"):
        raise RuntimeError("Original funding PaymentIntent is unavailable.")
    return intent


def _apply_refund(row, refund):
    if (refund.get("amount") != row.refund_amount_cents or refund.get("currency") != row.currency
            or refund.get("payment_intent") != row.payment_intent_id):
        raise RuntimeError("Refund details do not match the frozen settlement.")
    row.stripe_refund_id = refund["id"]
    status = refund.get("status")
    row.status = "refunded" if status == "succeeded" else "manual_review" if status in {"failed", "canceled"} else "refund_pending"
    row.last_error = "Stripe refund requires manual reconciliation." if row.status == "manual_review" else None
    row.next_attempt_at = datetime.utcnow() + timedelta(minutes=5)


def process_refund(db, settlement_id, *, now=None):
    now = now or datetime.utcnow()
    found = db.get(StudySettlement, settlement_id)
    if not found:
        return
    study = lock_study(db, found.survey_id)
    row = db.query(StudySettlement).filter_by(id=settlement_id).populate_existing().with_for_update().one()
    if row.status not in {"ready", "retrying", "refund_pending"}:
        return
    if row.next_attempt_at and row.next_attempt_at > now:
        return
    row.attempts += 1
    try:
        if not stripe.api_key:
            raise RuntimeError("Stripe is not configured; refund has not been submitted.")
        if row.stripe_refund_id:
            _apply_refund(row, stripe.Refund.retrieve(row.stripe_refund_id))
        else:
            row.payment_intent_id = _funding_intent(study, row)
            # Search by our durable marker before every retry. Stripe only keeps
            # idempotency keys for a limited time, so a key alone is insufficient.
            matching = None
            for refund in stripe.Refund.list(payment_intent=row.payment_intent_id, limit=100).auto_paging_iter():
                if refund.get("metadata", {}).get("insighta_settlement_id") == str(row.id):
                    matching = refund
                    break
            if matching is None:
                matching = stripe.Refund.create(payment_intent=row.payment_intent_id,
                    amount=row.refund_amount_cents, reason="requested_by_customer",
                    metadata={"insighta_settlement_id": str(row.id), "survey_id": str(study.id)},
                    idempotency_key=f"study-settlement:{row.id}:v1")
            _apply_refund(row, matching)
    except Exception:
        # Do not expose provider credentials or customer details in UI/logs.
        row.status = "manual_review" if row.attempts >= 5 else "retrying"
        row.last_error = "Refund could not be confirmed. It will retry or require support review."
        row.next_attempt_at = now + timedelta(minutes=5 * row.attempts)
    row.updated_at = now
    db.commit()


def run_settlement_cycle(db, verification_satisfies, *, now=None, limit=25):
    now = now or datetime.utcnow()
    scan_settlements(db, verification_satisfies, now=now)
    ids = db.query(StudySettlement.id).filter(StudySettlement.status.in_(["ready", "retrying", "refund_pending"]),
        or_(StudySettlement.next_attempt_at.is_(None), StudySettlement.next_attempt_at <= now)).order_by(StudySettlement.id).limit(limit).all()
    for (settlement_id,) in ids:
        process_refund(db, settlement_id, now=now)
    return len(ids)


def settlement_payload(row):
    if not row:
        return None
    return {"id": row.id, "status": row.status, "close_reason": row.close_reason,
        "ordered_count": row.pricing_snapshot["target_responses"], "qualified_count": row.qualified_count,
        "refund_amount_cents": row.refund_amount_cents, "currency": row.currency,
        "last_error": row.last_error, "stripe_refund_id": row.stripe_refund_id,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None}
