"""Screening decision data structure."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScreeningDecision:
    status: str  # passed | failed | review
    risk_score: int
    reasons: tuple[str, ...]
