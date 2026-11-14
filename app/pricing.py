"""Authoritative recruitment quotes. All monetary arithmetic uses integer cents."""
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path


class PricingError(ValueError):
    pass


def pricing_config():
    path = Path(os.environ.get("INSIGHTA_PRICING_CONFIG") or Path(__file__).parent / "config/pricing.json")
    return json.loads(path.read_text())


def money_cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("999999.99"):
            raise PricingError("Reward must be between $0 and $999,999.99.")
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise PricingError("Use at most two decimal places for the reward.")
        return int(cents)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingError("Enter a valid reward with at most two decimal places.") from exc


def positive_count(value):
    try:
        count = int(value)
        if isinstance(value, bool) or str(value) != str(count) or not 1 <= count <= 100000:
            raise ValueError
        return count
    except (TypeError, ValueError):
        raise PricingError("Participant count must be a whole number between 1 and 100,000.")


def normalize_study_type(value):
    aliases = {"external": "survey", "builtin": "survey", "online_interview": "interview",
               "remote_interview": "interview", "in_person_study": "in_person", "ux_test": "survey", "diary_study": "survey"}
    return aliases.get(value, value)


def _bps(amount, bps):
    return (amount * bps + 5000) // 10000


def calculate_quote(study_type, incentive_type, per_person, target_responses, urgency_level="flexible"):
    config = pricing_config()
    if not all(isinstance(value, str) for value in (study_type, incentive_type, urgency_level or "flexible")):
        raise PricingError("Study type, incentive, and timeline must be text values.")
    study_type = normalize_study_type(study_type)
    if study_type not in config["study_types"]:
        raise PricingError("Choose survey, remote interview, or in-person study. Niche studies require a manual quote.")
    if incentive_type not in {"cash", "gift_card", "raffle", "volunteer"}:
        raise PricingError("Choose a valid incentive type.")
    urgency_level = urgency_level or "flexible"
    if urgency_level not in config["urgency_bps"]:
        raise PricingError("Choose a valid recruitment timeline.")
    count = positive_count(target_responses)
    rule = config["study_types"][study_type]
    reward = money_cents(per_person) if incentive_type in {"cash", "gift_card"} else 0
    if incentive_type in {"cash", "gift_card"} and reward <= 0:
        raise PricingError("Enter a positive participant reward.")
    percentage_fee = _bps(reward, rule["rate_bps"])
    fee = max(percentage_fee, rule["minimum_fee_cents"])
    if incentive_type in {"raffle", "volunteer"}:
        policy = config["non_cash_policy"][incentive_type]
        if policy == "minimum_per_person":
            fee = rule["minimum_fee_cents"]
        else:
            raise PricingError("Unsupported non-cash pricing policy.")
    subtotal = count * (reward + fee)
    urgency_bps = config["urgency_bps"][urgency_level]
    urgency = _bps(subtotal, urgency_bps)
    return {"version": config["version"], "currency": config["currency"],
            "study_type": study_type, "incentive_type": incentive_type,
            "target_responses": count, "urgency_level": urgency_level,
            "reward_per_person_cents": reward, "recruitment_fee_per_person_cents": fee,
            "rate_bps": rule["rate_bps"], "minimum_fee_cents": rule["minimum_fee_cents"],
            "minimum_applied": percentage_fee < rule["minimum_fee_cents"],
            "urgency_bps": urgency_bps, "subtotal_cents": subtotal,
            "urgency_amount_cents": urgency, "total_cents": subtotal + urgency}
