"""Minimal Didit Session API client for webhook reconciliation."""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

DIDIT_API_BASE = (
    os.environ.get("DIDIT_API_BASE_URL") or "https://verification.didit.me"
).rstrip("/")


def didit_api_key() -> str:
    return (os.environ.get("DIDIT_API_KEY") or "").strip()


def fetch_session_decision(session_id: str, *, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch canonical session status from Didit (fallback when webhook is missed)."""
    api_key = didit_api_key()
    if not api_key:
        raise RuntimeError("DIDIT_API_KEY is not configured")
    url = f"{DIDIT_API_BASE}/v3/session/{session_id}/decision/"
    with httpx.Client(timeout=timeout) as client:
        response = client.get(
            url,
            headers={
                "x-api-key": api_key,
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Didit decision response was not a JSON object")
    return payload
