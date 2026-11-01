"""Didit session creation and decision retrieval; credentials stay server-side."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

DIDIT_API_BASE = (
    os.environ.get("DIDIT_API_BASE_URL") or "https://verification.didit.me"
).rstrip("/")


def didit_api_key() -> str:
    return (os.environ.get("DIDIT_API_KEY") or "").strip()


def didit_configured() -> bool:
    return bool(
        didit_api_key()
        and (os.environ.get("DIDIT_WORKFLOW_ID") or "").strip()
        and (os.environ.get("DIDIT_WEBHOOK_SECRET") or "").strip()
    )


def verification_url(value: str) -> str:
    """Only redirect to the hosted provider or an explicitly configured custom host."""
    parsed = urlsplit(value)
    hosts = {"verify.didit.me"}
    hosts.update(h.strip().lower() for h in os.environ.get("DIDIT_VERIFICATION_HOSTS", "").split(",") if h.strip())
    if (
        parsed.scheme != "https" or parsed.hostname not in hosts
        or parsed.username or parsed.password or parsed.port not in {None, 443}
    ):
        raise ValueError("Didit returned an invalid verification URL")
    return value


def create_session(*, user_id: int, callback: str, timeout: float = 15.0) -> dict[str, Any]:
    """Create (or resume) a session with account binding supplied only by the server."""
    if not didit_configured():
        raise RuntimeError("Didit verification is not configured")
    workflow_id = os.environ["DIDIT_WORKFLOW_ID"].strip()
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{DIDIT_API_BASE}/v3/session/",
            headers={"x-api-key": didit_api_key(), "Accept": "application/json"},
            json={"workflow_id": workflow_id, "vendor_data": str(user_id), "callback": callback},
        )
        response.raise_for_status()
        payload = response.json()
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("session_id"), str) or not payload["session_id"].strip()
        or not isinstance(payload.get("url"), str)
    ):
        raise ValueError("Didit returned an invalid session")
    if str(payload.get("vendor_data")) != str(user_id) or payload.get("workflow_id") != workflow_id:
        raise ValueError("Didit returned a session for a different account or workflow")
    verification_url(payload["url"])
    return payload


def fetch_session_decision(session_id: str, *, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch canonical session status from Didit (fallback when webhook is missed)."""
    api_key = didit_api_key()
    if not api_key:
        raise RuntimeError("DIDIT_API_KEY is not configured")
    url = f"{DIDIT_API_BASE}/v3/session/{quote(session_id, safe='')}/decision/"
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
