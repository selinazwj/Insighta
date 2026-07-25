"""Small observability helpers for request timing and startup diagnostics."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import Request


DEFAULT_SLOW_REQUEST_MS = 1000.0


def configure_logging() -> None:
    level_name = os.environ.get("INSIGHTA_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("insighta").setLevel(level)


def slow_request_threshold_ms() -> float:
    raw = os.environ.get("INSIGHTA_SLOW_REQUEST_MS")
    if not raw:
        return DEFAULT_SLOW_REQUEST_MS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_SLOW_REQUEST_MS


def request_id_from(request: Request) -> Optional[str]:
    value = request.headers.get("x-request-id") or request.headers.get("x-vercel-id")
    return value[:160] if value else None


def request_timer_start() -> float:
    return time.perf_counter()


def request_duration_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0
