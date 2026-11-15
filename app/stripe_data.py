"""Convert Stripe API resources to plain data at integration boundaries."""

def plain_stripe_data(value):
    if isinstance(value, dict):
        return {key: plain_stripe_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_stripe_data(item) for item in value]
    convert = getattr(value, 'to_dict', None)
    if callable(convert):
        return plain_stripe_data(convert())
    return value
