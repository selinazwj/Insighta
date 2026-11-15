"""Manual offers use the same cent-based pricing snapshot and settlement contract."""
from datetime import datetime, timedelta, timezone
import stripe
from fastapi import HTTPException
from app.models import Survey
from app.niche_models import NicheRequest, NicheQuote
from app.pricing import calculate_quote, money_cents, PricingError
from app.stripe_data import plain_stripe_data

FOUNDER_EMAIL = 'vfsa@bu.edu'


def request_for(db, survey_id):
    return db.query(NicheRequest).filter_by(survey_id=survey_id).first()


def locked_study(db, survey_id):
    return db.query(Survey).filter_by(id=survey_id).populate_existing().with_for_update().first()


def assert_editable(db, study):
    if request_for(db, study.id):
        raise HTTPException(409, 'This niche request has been submitted. Contact our founder to revise the requirements.')


def submit_request(db, study, requirements):
    previous = request_for(db, study.id)
    if previous:
        return previous
    row = NicheRequest(survey_id=study.id, requirements=requirements, status='requested')
    study.pricing_snapshot = None
    study.payment_status = 'quote_requested'
    study.status = 'draft'
    db.add(row)
    db.flush()
    return row


def manual_snapshot(study, body):
    try:
        # Reuse input validation, currency, minimums and urgency rounding.
        reward = money_cents(body.get('reward'))
        base = calculate_quote(study.task_type, study.incentive_type or 'cash',
                               body.get('reward'), study.target_responses, study.urgency_level)
        fee = money_cents(body.get('fee'))
        if fee < base['minimum_fee_cents']:
            raise PricingError(f"Platform fee must be at least ${base['minimum_fee_cents']/100:.2f} per participant.")
        if study.incentive_type in {'volunteer', 'raffle'} and reward:
            raise PricingError('Volunteer and raffle quotes have no per-participant cash reward.')
        subtotal = base['target_responses'] * (base['reward_per_person_cents'] + fee)
        urgency = (subtotal * base['urgency_bps'] + 5000) // 10000
        if subtotal + urgency > 99999999:
            raise PricingError('Quote total exceeds the supported payment limit.')
        return {**base, 'version':str(base['version']) + ':niche-v1', 'price_source':'manual_niche',
                'rate_bps':0, 'recruitment_fee_per_person_cents':fee,
                'minimum_applied':fee == base['minimum_fee_cents'],
                'subtotal_cents':subtotal, 'urgency_amount_cents':urgency, 'total_cents':subtotal+urgency}
    except PricingError as exc:
        raise HTTPException(400, str(exc))


def offer_quote(db, study, body):
    study = locked_study(db, study.id)
    row = request_for(db, study.id)
    if not row or study.payment_status == 'paid' or study.status != 'draft':
        raise HTTPException(409, 'Only unpaid niche requests can receive a quote.')
    snapshot = manual_snapshot(study, body)
    notes = body.get('notes', '')
    if not isinstance(notes, str) or len(notes) > 2000:
        raise HTTPException(400, 'Quote notes must be at most 2,000 characters.')
    try:
        days = int(body.get('valid_days', 7))
        if not 1 <= days <= 30 or str(days) != str(body.get('valid_days', 7)):
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(400, 'Quote validity must be 1–30 whole days.')
    old = db.get(NicheQuote, row.current_quote_id) if row.current_quote_id else None
    if old and old.status == 'checkout_creating':
        raise HTTPException(409, 'Checkout is being reconciled. Retry its payment link before revising this quote.')
    if old and old.stripe_session_id:
        try:
            session = plain_stripe_data(stripe.checkout.Session.retrieve(old.stripe_session_id))
            if session.get('status') == 'complete' or session.get('payment_status') == 'paid':
                raise HTTPException(409, 'Payment has completed. Wait for its confirmation before making changes.')
            if session.get('status') != 'expired':
                expired = plain_stripe_data(stripe.checkout.Session.expire(old.stripe_session_id))
                if expired.get('status') != 'expired':
                    raise HTTPException(409, 'The previous checkout is not closed yet. Retry before revising its quote.')
        except stripe.error.StripeError:
            raise HTTPException(502, 'Could not close the previous checkout. Its quote is unchanged; retry later.')
    if old:
        old.status = 'superseded'
    quote = NicheQuote(request_id=row.id, snapshot=snapshot, notes=notes.strip(),
                       expires_at=datetime.utcnow()+timedelta(days=days), status='offered')
    db.add(quote)
    db.flush()
    row.current_quote_id, row.status = quote.id, 'quoted'
    study.payment_status = 'quote_ready'
    return quote


def checkout_for_quote(db, study, quote_id, destination, base_url):
    study = locked_study(db, study.id)
    row = request_for(db, study.id)
    quote = db.get(NicheQuote, quote_id)
    if not row or not quote or row.current_quote_id != quote.id or quote.request_id != row.id:
        raise HTTPException(409, 'This quote has been replaced. Open the latest quote.')
    if study.payment_status == 'paid':
        return f'/publisher/study/{study.id}'
    if study.status != 'draft':
        raise HTTPException(409, 'This request is no longer available for payment.')
    now = datetime.utcnow()
    if quote.expires_at <= now:
        raise HTTPException(409, 'This quote has expired. Contact our founder for a new quote.')
    if study.deadline_at and study.deadline_at <= now:
        raise HTTPException(409, 'The study deadline has passed. Contact our founder to revise this request.')
    if destination not in {'wallet', 'card'}:
        raise HTTPException(400, 'Choose a valid refund destination.')
    if quote.refund_destination and destination != quote.refund_destination:
        raise HTTPException(409, 'This checkout already saved a refund destination. You can change it after payment, before settlement.')
    if quote.stripe_session_id:
        if quote.checkout_expires_at <= now:
            raise HTTPException(409, 'This payment link has expired. Contact our founder for a new quote.')
        return quote.checkout_url
    if not stripe.api_key:
        raise HTTPException(503, 'Card payment is unavailable. Your quote is saved.')
    if not quote.checkout_expires_at:
        expires = min(quote.expires_at, now+timedelta(hours=23), study.deadline_at or quote.expires_at)
        if expires-now < timedelta(minutes=31):
            raise HTTPException(409, 'This quote expires soon. Request a new quote before paying.')
        quote.checkout_expires_at = expires.replace(microsecond=0)
        quote.refund_destination = destination
        quote.status = 'checkout_creating'
        db.commit()  # Durable identity and parameters survive unknown provider outcomes.
        study = locked_study(db, study.id)
        row = request_for(db, study.id)
        quote = db.query(NicheQuote).filter_by(id=quote_id).populate_existing().one()
        if row.current_quote_id != quote.id:
            raise HTTPException(409, 'Open the latest quote.')
        if quote.stripe_session_id:
            return quote.checkout_url
    try:
        session = plain_stripe_data(stripe.checkout.Session.create(
            mode='payment', payment_method_types=['card'],
            line_items=[{'price_data':{'currency':quote.snapshot['currency'], 'unit_amount':quote.snapshot['total_cents'],
                         'product_data':{'name':f'Insighta niche study #{study.id}',
                                         'description':f"{study.target_responses} participants · Quote #{quote.id}"}}, 'quantity':1}],
            success_url=base_url.rstrip('/')+f'/publisher/study/{study.id}/quote?payment=processing',
            cancel_url=base_url.rstrip('/')+f'/publisher/study/{study.id}/quote',
            expires_at=int(quote.checkout_expires_at.replace(tzinfo=timezone.utc).timestamp()),
            metadata={'niche_quote_id':str(quote.id), 'survey_id':str(study.id), 'publisher_id':str(study.publisher_id)},
            idempotency_key=f'niche-quote:{quote.id}:v1'))
    except stripe.error.StripeError:
        raise HTTPException(502, 'Checkout could not be confirmed. Retry this same quote; no listing has been published.')
    quote.stripe_session_id, quote.checkout_url = session['id'], session['url']
    quote.status = 'payment_pending'
    db.flush()
    return quote.checkout_url


def fulfill_checkout(db, checkout):
    metadata = checkout.get('metadata') or {}
    try:
        study_id = int(metadata.get('survey_id', 0))
    except (TypeError, ValueError):
        return bool(metadata.get('niche_quote_id'))
    study = locked_study(db, study_id)
    row = request_for(db, study_id) if study else None
    if not row:
        return bool(metadata.get('niche_quote_id'))
    quote = db.get(NicheQuote, row.current_quote_id) if row.current_quote_id else None
    if (not quote or str(quote.id) != metadata.get('niche_quote_id')
            or metadata.get('publisher_id') != str(study.publisher_id)
            or checkout.get('payment_status') != 'paid'
            or checkout.get('amount_total') != quote.snapshot['total_cents']
            or checkout.get('currency') != quote.snapshot['currency']
            or not isinstance(checkout.get('payment_intent'), str)
            or not checkout['payment_intent'].startswith('pi_')):
        return True  # Never fall through to legacy publication for a niche request.
    if not quote.stripe_session_id and quote.status == 'checkout_creating':
        # Stripe can accept Checkout creation while its HTTP response is lost.
        # Recover a paid session from the signed event, rather than acknowledging
        # and dropping the only payment notification before an owner retries.
        try:
            recovered = plain_stripe_data(stripe.checkout.Session.retrieve(checkout.get('id')))
        except stripe.error.StripeError:
            raise HTTPException(503, 'Payment confirmation is being reconciled. Stripe should retry.')
        if any(recovered.get(key) != checkout.get(key) for key in ('id', 'payment_status', 'amount_total', 'currency', 'payment_intent')):
            raise HTTPException(503, 'Payment confirmation is not yet consistent. Stripe should retry.')
        if any((recovered.get('metadata') or {}).get(key) != metadata.get(key) for key in ('survey_id', 'publisher_id', 'niche_quote_id')):
            return True
        quote.stripe_session_id, quote.checkout_url = recovered['id'], recovered.get('url')
    if checkout.get('id') != quote.stripe_session_id:
        return True
    if study.payment_status == 'paid':
        return True
    study.pricing_snapshot = dict(quote.snapshot)
    study.reward_amount = study.per_person_gross = quote.snapshot['reward_per_person_cents']/100
    study.total_budget = quote.snapshot['total_cents']/100
    study.commission_rate = 0
    study.stripe_payment_intent_id = quote.stripe_session_id
    study.stripe_funding_payment_intent_id = checkout.get('payment_intent')
    study.funding_source, study.refund_destination = 'stripe', quote.refund_destination
    study.payment_status, study.status, study.published_at = 'paid', 'published', datetime.utcnow()
    quote.status, row.status = 'paid', 'paid'
    from app.wallet import issue_study_invoice
    issue_study_invoice(db, study)
    return True
