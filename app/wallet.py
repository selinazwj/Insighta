"""Integer-cent researcher wallet accounting and funding-source-preserving refunds."""
from datetime import datetime
import re
from sqlalchemy import func, update
from fastapi import HTTPException
import stripe
from app.stripe_data import plain_stripe_data

from app.models import User, Survey
from app.wallet_models import (WalletAccount, WalletLedger, WalletTopUp, WalletFundingLot, WalletCoupon,
    WalletPromoGrant, WalletPayment, WalletAllocation, WalletPromoUse, WalletRefundPart)


def lock_wallet(db, user_id, *, allow_blocked=False):
    # PostgreSQL row locks serialize all cash/grant mutations for this researcher.
    # SQLite has no SELECT FOR UPDATE, so obtain its write lock explicitly.
    if db.bind.dialect.name == 'sqlite':
        db.execute(update(User).where(User.id == user_id).values(id=User.id))
    user = db.query(User).filter_by(id=user_id).populate_existing().with_for_update().first()
    if not user: raise HTTPException(404, 'Account not found')
    account = db.query(WalletAccount).filter_by(user_id=user_id).populate_existing().first()
    if not account:
        account = WalletAccount(user_id=user_id, blocked=False)
        db.add(account); db.flush()
    if account.blocked and not allow_blocked:
        raise HTTPException(409, 'Wallet payments are paused pending payment reconciliation. Contact support.')
    return user, account


def cash_balance(db, user_id):
    return int(db.query(func.coalesce(func.sum(WalletLedger.delta_cents), 0)).filter_by(user_id=user_id).scalar())


def active_grants(db, user_id, now=None):
    return db.query(WalletPromoGrant).filter(WalletPromoGrant.user_id == user_id,
        WalletPromoGrant.expires_at > (now or datetime.utcnow()), WalletPromoGrant.remaining_cents > 0
    ).order_by(WalletPromoGrant.expires_at, WalletPromoGrant.id).all()


def summary(db, user_id):
    account = db.get(WalletAccount, user_id)
    return {'cash_cents': cash_balance(db, user_id),
            'promo_cents': sum(g.remaining_cents for g in active_grants(db, user_id)),
            'currency': 'usd', 'blocked': bool(account and account.blocked),
            'billing_profile': account.billing_profile if account else {}}


def payment_quote(db, user_id, snapshot, use_promo=True):
    eligible = snapshot['target_responses'] * snapshot['recruitment_fee_per_person_cents']
    available = sum(g.remaining_cents for g in active_grants(db, user_id)) if use_promo else 0
    promo = min(eligible, available)
    cash = snapshot['total_cents'] - promo
    return {'promo_applied_cents': promo, 'cash_due_cents': cash,
            'cash_balance_cents': cash_balance(db, user_id), 'promo_eligible_cents': eligible}


def _cash_entry(db, user_id, delta, kind, reference, description):
    if delta:
        db.add(WalletLedger(user_id=user_id, delta_cents=delta, kind=kind, reference=reference, description=description))


def fulfill_topup(db, session):
    session = plain_stripe_data(session)
    marker = str(session.get('metadata', {}).get('wallet_topup_id', ''))
    if not marker.isdigit(): return False
    found = db.get(WalletTopUp, int(marker))
    if not found: return True
    user, account = lock_wallet(db, found.user_id, allow_blocked=True)
    topup = db.query(WalletTopUp).filter_by(id=found.id).populate_existing().with_for_update().one()
    if topup.status == 'paid': return True
    if not topup.stripe_session_id:
        # A signed webhook may arrive after Stripe accepted Checkout but the
        # create response was lost. Recover only an authoritative matching session.
        session_id = session.get('id')
        if not stripe.api_key or not isinstance(session_id,str) or not session_id.startswith('cs_'):
            raise HTTPException(503,'Top-up payment reconciliation is pending.')
        session = plain_stripe_data(stripe.checkout.Session.retrieve(session_id))
        if (session.get('id') != session_id or session.get('mode') != 'payment'
                or str(session.get('metadata',{}).get('wallet_topup_id')) != str(topup.id)):
            raise HTTPException(400,'Top-up funding does not match the saved order.')
        topup.stripe_session_id = session_id
    intent = session.get('payment_intent')
    if (session.get('id') != topup.stripe_session_id or session.get('payment_status') != 'paid'
        or session.get('currency') != 'usd' or session.get('amount_total') != topup.amount_cents
        or str(session.get('metadata', {}).get('user_id')) != str(user.id)
        or not isinstance(intent, str) or not intent.startswith('pi_')):
        raise HTTPException(400, 'Top-up funding does not match the saved order.')
    topup.payment_intent_id, topup.status, topup.paid_at = intent, 'paid', datetime.utcnow()
    reference = f'topup:{topup.id}'
    db.add(WalletFundingLot(user_id=user.id, reference=reference, payment_intent_id=intent,
                           credited_cents=topup.amount_cents, available_cents=topup.amount_cents))
    _cash_entry(db, user.id, topup.amount_cents, 'topup', reference, f'Wallet top-up #{topup.id}')
    from app.invoices import issue_invoice
    issue_invoice(db, user, reference, f'Wallet top-up #{topup.id}',
        [{'description':'Research recruitment wallet funding', 'amount_cents':topup.amount_cents}],
        topup.amount_cents, 'Stripe card', document_kind='topup_receipt')
    db.flush()
    return True


def redeem_coupon(db, user_id, code):
    lock_wallet(db, user_id)
    normalized = str(code or '').strip().upper()
    coupon = db.query(WalletCoupon).filter_by(code=normalized).populate_existing().with_for_update().first()
    now = datetime.utcnow()
    if not coupon or coupon.expires_at <= now or coupon.redemption_count >= coupon.max_redemptions:
        raise HTTPException(400, 'This coupon is invalid, expired, or fully redeemed.')
    if db.query(WalletPromoGrant).filter_by(user_id=user_id, coupon_id=coupon.id).first():
        raise HTTPException(409, 'This account has already redeemed this coupon.')
    grant = WalletPromoGrant(user_id=user_id, coupon_id=coupon.id, amount_cents=coupon.amount_cents,
        remaining_cents=coupon.amount_cents, expires_at=coupon.expires_at)
    coupon.redemption_count += 1
    db.add(grant); db.flush()
    return grant


def pay_study(db, study_id, user_id, *, use_promo=True, expected_snapshot=None):
    from app.settlements import lock_study
    if db.bind.dialect.name == 'sqlite':
        db.execute(update(Survey).where(Survey.id == study_id).values(id=Survey.id))
    study = lock_study(db, study_id)
    if not study or study.publisher_id != user_id: raise HTTPException(404, 'Study not found')
    existing = db.query(WalletPayment).filter_by(survey_id=study.id).first()
    if existing: return existing
    if expected_snapshot is not None and study.pricing_snapshot != expected_snapshot:
        raise HTTPException(409, 'This draft changed in another request. Review its quote before paying.')
    if study.payment_status != 'unpaid' or study.status != 'draft' or not study.pricing_snapshot:
        raise HTTPException(409, 'Only an unpaid quoted draft can be paid from this wallet.')
    # Never switch a live card checkout to wallet while it could still be paid.
    if study.stripe_payment_intent_id:
        if not stripe.api_key: raise HTTPException(503, 'Reconcile the existing Stripe checkout before switching payment method.')
        checkout = plain_stripe_data(stripe.checkout.Session.retrieve(study.stripe_payment_intent_id))
        if checkout.get('payment_status') == 'paid': raise HTTPException(409, 'This checkout is already paid; wait for confirmation.')
        if checkout.get('status') == 'open': stripe.checkout.Session.expire(study.stripe_payment_intent_id)
        elif checkout.get('status') != 'expired': raise HTTPException(409, 'Existing card checkout needs reconciliation.')
    user, account = lock_wallet(db, user_id)
    snapshot = dict(study.pricing_snapshot)
    price = payment_quote(db, user_id, snapshot, use_promo)
    lots = db.query(WalletFundingLot).filter(WalletFundingLot.user_id == user_id,
        WalletFundingLot.available_cents > 0).order_by(WalletFundingLot.id).all()
    if sum(lot.available_cents for lot in lots) != price['cash_balance_cents']:
        raise HTTPException(409, 'Wallet funding requires reconciliation. Contact support.')
    if price['cash_due_cents'] > price['cash_balance_cents']:
        raise HTTPException(409, 'Insufficient wallet cash balance. Add funds or choose Stripe checkout.')
    payment = WalletPayment(user_id=user_id, survey_id=study.id, cash_cents=price['cash_due_cents'],
        promo_cents=price['promo_applied_cents'], pricing_snapshot=snapshot)
    db.add(payment); db.flush()
    remaining = payment.cash_cents
    for lot in lots:
        used = min(remaining, lot.available_cents)
        if used:
            lot.available_cents -= used
            db.add(WalletAllocation(payment_id=payment.id, lot_id=lot.id, amount_cents=used))
            remaining -= used
        if not remaining: break
    promo_remaining = payment.promo_cents
    for grant in active_grants(db, user_id):
        used = min(promo_remaining, grant.remaining_cents)
        if used:
            grant.remaining_cents -= used
            db.add(WalletPromoUse(payment_id=payment.id, grant_id=grant.id, amount_cents=used))
            promo_remaining -= used
        if not promo_remaining: break
    _cash_entry(db, user_id, -payment.cash_cents, 'study_payment', f'study-payment:{study.id}', study.title)
    study.funding_source, study.payment_status, study.status = 'wallet', 'paid', 'published'
    study.published_at = datetime.utcnow()
    issue_study_invoice(db, study, user=user, payment=payment)
    db.flush()
    return payment


def issue_study_invoice(db, study, user=None, payment=None):
    if not study.pricing_snapshot: return None
    from app.invoices import issue_invoice
    user = user or db.get(User, study.publisher_id)
    payment = payment or db.query(WalletPayment).filter_by(survey_id=study.id).first()
    q = study.pricing_snapshot
    count = q['target_responses']
    lines = [
        {'description':f'Participant rewards: {count} x ${q["reward_per_person_cents"]/100:.2f}', 'amount_cents':count*q['reward_per_person_cents']},
        {'description':f'Platform recruitment: {count} x ${q["recruitment_fee_per_person_cents"]/100:.2f}', 'amount_cents':count*q['recruitment_fee_per_person_cents']},
        {'description':'Urgency surcharge', 'amount_cents':q['urgency_amount_cents']}]
    if payment and payment.promo_cents: lines.append({'description':'Promotional recruitment credit (non-refundable)', 'amount_cents':-payment.promo_cents})
    return issue_invoice(db, user, f'study:{study.id}', study.title, lines,
        payment.cash_cents if payment else q['total_cents'], 'Wallet' if payment else 'Stripe card')


def settlement_cash_refund(db, study, gross, qualified_count, reason):
    payment = db.query(WalletPayment).filter_by(survey_id=study.id).first()
    if not payment: return gross
    q = payment.pricing_snapshot
    unused = max(0, q['target_responses']-qualified_count)
    burned_promo = 0 if reason == 'researcher_cancelled' else (
        payment.promo_cents*unused + q['target_responses']//2)//q['target_responses']
    return min(payment.cash_cents, max(0, gross-burned_promo))


def proportional_parts(amount, allocations):
    total = sum(a.amount_cents for a in allocations)
    if amount < 0 or amount > total: raise ValueError('Refund exceeds original wallet funding.')
    if not total: return []
    parts = [[a, amount*a.amount_cents//total] for a in allocations]
    remainder = amount-sum(value for _, value in parts)
    for part in parts:
        if not remainder: break
        if part[1] < part[0].amount_cents:
            part[1] += 1; remainder -= 1
    return [(a, value) for a, value in parts if value]


def apply_part(part, refund):
    refund = plain_stripe_data(refund)
    if (refund.get('amount') != part.amount_cents or refund.get('currency') != 'usd'
        or refund.get('payment_intent') != part.payment_intent_id):
        raise ValueError('Provider refund does not match its funding allocation.')
    part.stripe_refund_id = refund['id']
    part.status = 'refunded' if refund.get('status') == 'succeeded' else (
        'manual_review' if refund.get('status') in {'failed','canceled'} else 'pending')


def refresh_parts_status(db, row):
    db.flush()
    parts = db.query(WalletRefundPart).filter_by(settlement_id=row.id).all()
    if parts and all(p.status == 'refunded' for p in parts): row.status = 'refunded'
    elif any(p.status == 'manual_review' for p in parts): row.status = 'manual_review'
    else: row.status = 'refund_pending'


def process_wallet_settlement(db, study, row):
    """Called with the study and settlement locked; all cash writes are atomic."""
    payment = db.query(WalletPayment).filter_by(survey_id=study.id).first()
    destination = row.refund_destination or 'card'
    if not payment and destination != 'wallet': return False
    user, account = lock_wallet(db, study.publisher_id, allow_blocked=True)
    if destination == 'wallet':
        reference = f'settlement:{row.id}'
        if not db.query(WalletLedger).filter_by(reference=reference).first():
            if payment:
                allocations = db.query(WalletAllocation).filter_by(payment_id=payment.id).order_by(WalletAllocation.id).all()
                for allocation, amount in proportional_parts(row.refund_amount_cents, allocations):
                    lot = db.get(WalletFundingLot, allocation.lot_id)
                    lot.available_cents += amount
            else:
                from app.settlements import _funding_intent
                intent = _funding_intent(study, row)
                row.payment_intent_id = intent
                db.add(WalletFundingLot(user_id=user.id, reference=reference, payment_intent_id=intent,
                    credited_cents=row.refund_amount_cents, available_cents=row.refund_amount_cents))
            _cash_entry(db, user.id, row.refund_amount_cents, 'study_refund', reference, f'Unused funding: {study.title}')
        row.status, row.last_error = 'wallet_refunded', None
        db.flush()
        return True
    # A wallet order can span several top-ups/cards. Refund each original source.
    if not stripe.api_key: raise RuntimeError('Stripe is not configured.')
    parts = db.query(WalletRefundPart).filter_by(settlement_id=row.id).order_by(WalletRefundPart.id).all()
    if not parts:
        allocations = db.query(WalletAllocation).filter_by(payment_id=payment.id).order_by(WalletAllocation.id).all()
        for allocation, amount in proportional_parts(row.refund_amount_cents, allocations):
            lot = db.get(WalletFundingLot, allocation.lot_id)
            db.add(WalletRefundPart(settlement_id=row.id, lot_id=lot.id, amount_cents=amount,
                payment_intent_id=lot.payment_intent_id, status='ready'))
        # Persist the immutable allocation before any external refund request.
        db.commit()
        from app.settlements import lock_study
        lock_study(db, study.id)
        db.refresh(row)
        lock_wallet(db, study.publisher_id, allow_blocked=True)
        if row.status in {'refunded', 'wallet_refunded'}:
            return True
        parts = db.query(WalletRefundPart).filter_by(settlement_id=row.id).order_by(WalletRefundPart.id).all()
    for part in parts:
        if part.status in {'refunded','manual_review'}: continue
        if part.stripe_refund_id:
            refund = stripe.Refund.retrieve(part.stripe_refund_id)
        else:
            refund = next((r for r in stripe.Refund.list(payment_intent=part.payment_intent_id, limit=100).auto_paging_iter()
                if plain_stripe_data(r).get('metadata', {}).get('insighta_wallet_part_id') == str(part.id)), None)
            if refund is None:
                refund = stripe.Refund.create(payment_intent=part.payment_intent_id, amount=part.amount_cents,
                    reason='requested_by_customer', metadata={'insighta_wallet_part_id':str(part.id), 'survey_id':str(study.id)},
                    idempotency_key=f'wallet-refund-part:{part.id}:v1')
        apply_part(part, refund)
        db.flush()
    refresh_parts_status(db, row)
    return True


def freeze_wallet_for_payment_event(db, event):
    event = plain_stripe_data(event)
    obj = event.get('data', {}).get('object', {})
    intent = obj.get('payment_intent')
    if not isinstance(intent, str): return
    lots = db.query(WalletFundingLot).filter_by(payment_intent_id=intent).all()
    if event.get('type') == 'charge.refunded':
        expected = int(db.query(func.coalesce(func.sum(WalletRefundPart.amount_cents),0)).filter(
            WalletRefundPart.payment_intent_id==intent, WalletRefundPart.stripe_refund_id.isnot(None)).scalar())
        if obj.get('amount_refunded', 0) <= expected: return
    for user_id in sorted({lot.user_id for lot in lots}):
        _, account = lock_wallet(db, user_id, allow_blocked=True)
        account.blocked = True
