"""Durable, reconcilable participant transfers to Stripe connected accounts."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import stripe
from fastapi import HTTPException
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, update, func, text
from sqlalchemy.exc import IntegrityError
from app.models import Base, Response, User
from app.stripe_data import plain_stripe_data


class ParticipantWithdrawal(Base):
    __tablename__ = 'participant_withdrawals'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    active_key = Column(String, nullable=True, unique=True)
    amount_cents = Column(Integer, nullable=False)
    destination_account_id = Column(String, nullable=False)
    response_amounts = Column(JSON, nullable=False)
    other_earnings_cents = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default='ready')
    stripe_transfer_id = Column(String, nullable=True, unique=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


def cents(value):
    try:
        amount = Decimal(str(value or 0))
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation
        return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(409, 'Earnings need reconciliation before withdrawal. Contact support.')


def lock_participant(db, user_id):
    if db.bind.dialect.name == 'sqlite':
        # SQLite ignores FOR UPDATE. These service entry points own the clean
        # transaction, including after each durable commit before a provider call.
        db.rollback()
        db.execute(text('BEGIN IMMEDIATE'))
    return db.query(User).filter_by(id=user_id).populate_existing().with_for_update().one()


def active_withdrawal(db, user_id):
    return db.query(ParticipantWithdrawal).filter_by(active_key=str(user_id)).populate_existing().first()


def assert_not_reserved(db, response):
    row = active_withdrawal(db, response.participant_id)
    if row and str(response.id) in row.response_amounts:
        raise HTTPException(409, 'This reward is part of a withdrawal being reconciled; its review cannot change.')


def _manual(db, row, reason):
    row.status, row.last_error = 'manual_review', reason
    db.commit()
    raise HTTPException(409, 'This withdrawal needs support reconciliation. No new transfer will be created.')


def _result(row):
    return {'success':True, 'amount':row.amount_cents/100, 'transfer_id':row.stripe_transfer_id,
            'withdrawal_id':row.id, 'status':'transferred'}


def _find_transfer(row):
    cursor = None
    found = None
    # Search every matching page. A bounded incomplete search must never permit
    # creation after Stripe's idempotency window has elapsed.
    for _ in range(100):
        params = {'destination':row.destination_account_id,
                  'created':{'gte':int((row.created_at-timedelta(minutes=5)).replace(tzinfo=timezone.utc).timestamp())}, 'limit':100}
        if cursor:
            params['starting_after'] = cursor
        page = plain_stripe_data(stripe.Transfer.list(**params))
        matches = [item for item in page.get('data', [])
                   if (item.get('metadata') or {}).get('insighta_withdrawal_id') == str(row.id)]
        if len(matches) > 1 or (matches and found):
            raise HTTPException(409, 'Multiple provider transfers require support reconciliation.')
        if matches:
            found = matches[0]
        if not page.get('has_more'):
            return found
        data = page.get('data') or []
        if not data or not data[-1].get('id') or data[-1]['id'] == cursor:
            raise HTTPException(503, 'Provider transfer history is incomplete. Retry reconciliation later.')
        cursor = data[-1]['id']
    raise HTTPException(503, 'Provider transfer history is incomplete. Retry reconciliation later.')


def _finish(db, user, row, transfer):
    db.refresh(row)
    if row.status == 'paid':
        return _result(row)
    metadata = transfer.get('metadata') or {}
    if (not str(transfer.get('id', '')).startswith('tr_')
            or transfer.get('amount') != row.amount_cents or transfer.get('currency') != 'usd'
            or transfer.get('destination') != row.destination_account_id
            or metadata.get('insighta_withdrawal_id') != str(row.id)
            or metadata.get('participant_id') != str(user.id)
            or transfer.get('reversed') or transfer.get('amount_reversed', 0)):
        _manual(db, row, 'Provider transfer does not match the saved withdrawal.')
    responses = db.query(Response).filter(Response.id.in_([int(x) for x in row.response_amounts]),
                                         Response.participant_id == user.id).populate_existing().all()
    if (len(responses) != len(row.response_amounts)
            or any(r.payout_status not in {'approved', 'pending'}
                   or cents(r.payout_amount) != row.response_amounts[str(r.id)] for r in responses)
            or cents(user.pending_earnings) < row.amount_cents):
        row.stripe_transfer_id = transfer['id']
        _manual(db, row, 'Provider transfer exists; reserved earnings changed before reconciliation.')
    # An atomic claim also protects SQLite/local concurrent finalizers. On
    # PostgreSQL, the participant row lock serializes review and withdrawals.
    claimed = db.execute(update(ParticipantWithdrawal).where(ParticipantWithdrawal.id == row.id,
        ParticipantWithdrawal.status != 'paid').values(status='paid', active_key=None,
        stripe_transfer_id=transfer['id'], last_error=None), execution_options={'synchronize_session':False})
    if claimed.rowcount != 1:
        db.refresh(row)
        return _result(row)
    db.execute(update(User).where(User.id == user.id).values(
        pending_earnings=func.coalesce(User.pending_earnings, 0)-row.amount_cents/100,
        total_withdrawn=func.coalesce(User.total_withdrawn, 0)+row.amount_cents/100), execution_options={'synchronize_session':False})
    for response in responses:
        response.payout_status, response.stripe_transfer_id = 'paid', transfer['id']
    db.commit()
    db.refresh(row)
    return _result(row)


def withdraw(db, user_id):
    user = lock_participant(db, user_id)
    if user.stripe_onboarding_complete != 'true' or not str(user.stripe_account_id or '').startswith('acct_'):
        raise HTTPException(400, 'Please complete Stripe onboarding first.')
    if not stripe.api_key:
        raise HTTPException(503, 'Withdrawals are unavailable. No transfer has been requested.')
    row = active_withdrawal(db, user_id)
    if not row:
        amount = cents(user.pending_earnings)
        if amount < 100:
            raise HTTPException(400, 'Minimum withdrawal is $1.00.')
        responses = db.query(Response).filter_by(participant_id=user_id, status='completed').filter(
            Response.payout_status.in_(['approved', 'pending']), Response.payout_amount > 0).populate_existing().all()
        response_amounts = {str(r.id):cents(r.payout_amount) for r in responses}
        # Feedback awards and legacy non-study earnings are also in the trusted
        # participant balance. Preserve them explicitly, rather than blocking
        # valid withdrawals or marking unrelated responses as paid.
        other_earnings = amount-sum(response_amounts.values())
        if other_earnings < 0 or amount > 99999999:
            raise HTTPException(409, 'Your earnings balance needs reconciliation. Contact support before withdrawing.')
        row = ParticipantWithdrawal(user_id=user_id, active_key=str(user_id), amount_cents=amount,
                                    destination_account_id=user.stripe_account_id, response_amounts=response_amounts,
                                    other_earnings_cents=other_earnings)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = active_withdrawal(db, user_id)
            if not row:
                raise HTTPException(409, 'A withdrawal just completed. Refresh your balance before retrying.')
    row_id = row.id
    user = lock_participant(db, user_id)
    row = db.query(ParticipantWithdrawal).filter_by(id=row_id).populate_existing().one()
    if row.status == 'paid':
        return _result(row)
    if row.status == 'manual_review':
        raise HTTPException(409, 'This withdrawal needs support reconciliation. No new transfer will be created.')
    try:
        if row.attempts:
            existing = _find_transfer(row)
            if existing:
                return _finish(db, user, row, existing)
            if datetime.utcnow()-row.created_at >= timedelta(hours=20):
                _manual(db, row, 'No transfer found outside the safe idempotency retry window.')
        row.attempts += 1
        row.status, row.last_error = 'processing', None
        db.commit()  # Record the attempt before any money can move.
        user = lock_participant(db, user_id)
        row = db.query(ParticipantWithdrawal).filter_by(id=row_id).populate_existing().one()
        if row.status == 'paid':
            return _result(row)
        reserved = db.query(Response).filter(Response.id.in_([int(x) for x in row.response_amounts]),
            Response.participant_id == user_id).populate_existing().all()
        if (len(reserved) != len(row.response_amounts) or cents(user.pending_earnings) < row.amount_cents
                or any(r.payout_status not in {'approved', 'pending'}
                       or cents(r.payout_amount) != row.response_amounts[str(r.id)] for r in reserved)):
            _manual(db, row, 'Reserved earnings changed before a transfer was created.')
        try:
            transfer = plain_stripe_data(stripe.Transfer.create(amount=row.amount_cents, currency='usd',
                destination=row.destination_account_id, transfer_group=f'insighta-withdrawal-{row.id}',
                description=f'Insighta participant withdrawal #{row.id}',
                metadata={'insighta_withdrawal_id':str(row.id), 'participant_id':str(user_id)},
                idempotency_key=f'participant-withdrawal:{row.id}:v1'))
        except stripe.error.InvalidRequestError as exc:
            if exc.http_status in {400, 402}:
                # A definitive create rejection did not transfer funds. Do not
                # apply this rule to a failed history lookup/unknown outcome.
                row.status, row.active_key = 'failed', None
                row.last_error = 'Provider declined transfer creation; earnings are unchanged.'
                db.commit()
                raise HTTPException(409, 'Transfer was declined. Your earnings are unchanged; contact support before retrying.')
            raise
        return _finish(db, user, row, transfer)
    except stripe.error.StripeError:
        row.last_error = 'Provider response unavailable; retry this same withdrawal to reconcile.'
        db.commit()
        raise HTTPException(503, 'Withdrawal confirmation is pending. Retry to check this same transfer; your earnings remain reserved.')


def reconcile(db, withdrawal_id):
    row = db.get(ParticipantWithdrawal, withdrawal_id)
    if not row:
        raise HTTPException(404, 'Withdrawal not found.')
    user = lock_participant(db, row.user_id)
    db.refresh(row)
    if row.status == 'paid':
        return _result(row)
    if not stripe.api_key:
        raise HTTPException(503, 'Stripe reconciliation is unavailable.')
    try:
        transfer = _find_transfer(row)
    except stripe.error.StripeError:
        raise HTTPException(503, 'Transfer history is unavailable. No new transfer was created.')
    if not transfer:
        raise HTTPException(409, 'No matching transfer found. No new transfer was created; review the provider history manually.')
    return _finish(db, user, row, transfer)
