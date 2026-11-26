"""Screening choice normalization."""
from __future__ import annotations

from typing import Any


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).replace("|", ",").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        item = str(raw or "").strip()
        normalized = item.casefold()
        if item and normalized not in {"all", "any", "both"} and normalized not in seen:
            out.append(item)
            seen.add(normalized)
    return out
