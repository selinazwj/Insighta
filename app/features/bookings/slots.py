"""Features / bookings / slots."""

from typing import Optional
import json


def _parse_booking_slots(value: Optional[str]) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        if isinstance(parsed, str):
            return [parsed.strip()] if parsed.strip() else []
    except Exception:
        pass
    return [raw]


def _serialize_booking_slots(slots: list[str]) -> Optional[str]:
    cleaned = []
    seen = set()
    for slot in slots:
        label = str(slot or "").strip()
        if label and label not in seen:
            cleaned.append(label)
            seen.add(label)
    if not cleaned:
        return None
    return cleaned[0] if len(cleaned) == 1 else json.dumps(cleaned)


def _booking_slots_label(value: Optional[str]) -> str:
    return "; ".join(_parse_booking_slots(value))


__all__ = ['_parse_booking_slots', '_serialize_booking_slots', '_booking_slots_label']
