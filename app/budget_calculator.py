"""Planning-only budgets, using the same quotes as listing publication."""
from app.pricing import calculate_quote, money_cents, positive_count, pricing_config, PricingError
from app.wallet import payment_quote

AUDIENCES = {'broad', 'targeted', 'specialized'}


def estimate_budget(db, user_id, body):
    budget = money_cents(body.get('budget'))
    if budget <= 0:
        raise PricingError('Enter a positive total study budget.')
    count = positive_count(body.get('target_responses'))
    audience = body.get('audience', 'broad')
    if not isinstance(audience, str) or audience not in AUDIENCES:
        raise PricingError('Choose a valid audience specificity.')
    kind = body.get('study_type', 'survey')
    urgency = body.get('urgency_level', 'flexible')
    if not isinstance(urgency, str) or urgency not in pricing_config()['urgency_bps']:
        raise PricingError('Choose a valid recruitment timeline.')
    # Smallest positive cash reward establishes the minimum viable quote.
    minimum = calculate_quote(kind, 'cash', '0.01', count, urgency)
    if budget < minimum['total_cents']:
        return {'status': 'below_minimum', 'budget_cents': budget,
                'minimum_budget_cents': minimum['total_cents'], 'quote': minimum,
                'manual_quote_required': audience != 'broad', 'suggested_topup_cents': None}
    low, high = 1, budget // count
    quote = minimum
    while low <= high:
        cents = (low + high) // 2
        candidate = calculate_quote(kind, 'cash', f'{cents//100}.{cents%100:02d}', count, urgency)
        if candidate['total_cents'] <= budget:
            quote = candidate
            low = cents + 1
        else:
            high = cents - 1
    funding = payment_quote(db, user_id, quote, use_promo=True)
    gap = max(0, funding['cash_due_cents'] - funding['cash_balance_cents'])
    # Stripe's minimum wallet top-up is $1; a smaller gap still needs $1.
    topup = max(100, gap) if gap else 0
    manual = audience != 'broad'
    return {'status': 'manual_quote_required' if manual else 'estimated',
            'budget_cents': budget, 'quote': quote, 'funding': funding,
            'remaining_budget_cents': budget - quote['total_cents'],
            'baseline_topup_cents': topup, 'suggested_topup_cents': None if manual else topup,
            'manual_quote_required': manual}
