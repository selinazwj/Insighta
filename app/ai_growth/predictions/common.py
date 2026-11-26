"""Prediction cache time and batch utilities."""
from __future__ import annotations

from typing import Any
from typing import Iterable
from typing import Optional
from datetime import datetime
from app.ai_growth.llm import llm_cache_hours
from datetime import timedelta


def _now() -> datetime:
    return datetime.utcnow()

def _cache_expires_at() -> datetime:
    return _now() + timedelta(hours=llm_cache_hours())

def _chunked(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]

def _safe_rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
