"""Didit webhook signature verification and status mapping.

Privacy rule: never persist document numbers, photos, or PII from Didit
decision payloads — only session ids, event ids, and verification status.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Iterable, Mapping, Optional

DIDIT_METHOD = "didit"
DIDIT_ATTRIBUTE = "identity"

# Didit session labels → local Verification / DiditSession.status
_STATUS_MAP = {
    "approved": "verified",
    "declined": "rejected",
    "abandoned": "rejected",
    "in progress": "pending",
    "in review": "pending",
    "not started": "pending",
    "resubmitted": "pending",
    "kyc expired": "rejected",
}

_VENDOR_USER_RE = re.compile(r"^(?:user[_-]?)?(\d+)$", re.IGNORECASE)


def shorten_floats(data: Any) -> Any:
    """Match Didit: whole-valued floats serialise as ints."""
    if isinstance(data, dict):
        return {k: shorten_floats(v) for k, v in data.items()}
    if isinstance(data, list):
        return [shorten_floats(x) for x in data]
    if isinstance(data, float) and data.is_integer():
        return int(data)
    return data


def canonical_json_v2(body: Mapping[str, Any]) -> str:
    return json.dumps(
        shorten_floats(body),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def webhook_secrets() -> list[str]:
    """Current secret first, optional previous secret for 24h rotation window."""
    secrets: list[str] = []
    current = (os.environ.get("DIDIT_WEBHOOK_SECRET") or "").strip()
    previous = (os.environ.get("DIDIT_WEBHOOK_PREVIOUS_SECRET") or "").strip()
    if current:
        secrets.append(current)
    if previous and previous not in secrets:
        secrets.append(previous)
    return secrets


def _signature_candidates(
    headers: Mapping[str, str],
    name: str,
) -> list[str]:
    """Return primary and -Previous signature header values (case-insensitive)."""
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    values: list[str] = []
    for key in (name.lower(), f"{name.lower()}-previous"):
        value = (lowered.get(key) or "").strip()
        if value:
            values.append(value)
    return values


def verify_signature_v2(
    body: Mapping[str, Any],
    signature_header: str,
    timestamp_header: str,
    secret: str,
    *,
    now: Optional[float] = None,
) -> bool:
    if not signature_header or not timestamp_header or not secret:
        return False
    try:
        ts = int(timestamp_header)
    except (TypeError, ValueError):
        return False
    current = now if now is not None else time.time()
    if abs(current - ts) > 300:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        canonical_json_v2(body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature_header, expected)


def is_didit_test_webhook(headers: Mapping[str, str]) -> bool:
    """Didit console test deliveries set X-Didit-Test-Webhook: true."""
    lowered = {str(k).lower(): str(v).strip().lower() for k, v in headers.items()}
    return lowered.get("x-didit-test-webhook") in {"true", "1", "yes"}


def verify_didit_webhook(
    body: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    secrets: Optional[Iterable[str]] = None,
    now: Optional[float] = None,
) -> bool:
    """Verify Didit X-Signature-V2 (and -Previous) against configured secrets."""
    secret_list = [s for s in (secrets if secrets is not None else webhook_secrets()) if s]
    if not secret_list:
        return False
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    timestamp = (lowered.get("x-timestamp") or "").strip()
    if not timestamp:
        return False
    for signature in _signature_candidates(headers, "x-signature-v2"):
        for secret in secret_list:
            if verify_signature_v2(body, signature, timestamp, secret, now=now):
                return True
    return False


def map_provider_status(provider_status: Optional[str]) -> str:
    """Map Didit session status label to pending | verified | rejected."""
    key = (provider_status or "").strip().lower()
    return _STATUS_MAP.get(key, "pending")


def parse_vendor_user_id(
    vendor_data: Any = None,
    metadata: Any = None,
) -> Optional[int]:
    """Resolve Insighta user id from Didit vendor_data / metadata.user_id."""
    candidates: list[Any] = []
    if vendor_data is not None:
        candidates.append(vendor_data)
    if isinstance(metadata, dict):
        for key in ("user_id", "insighta_user_id", "participant_id"):
            if key in metadata:
                candidates.append(metadata.get(key))

    for raw in candidates:
        if raw is None:
            continue
        if isinstance(raw, int) and raw > 0:
            return raw
        text = str(raw).strip()
        if not text:
            continue
        match = _VENDOR_USER_RE.match(text)
        if match:
            return int(match.group(1))
        try:
            value = int(text)
        except ValueError:
            continue
        if value > 0:
            return value
    return None
