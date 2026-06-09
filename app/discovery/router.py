from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from .discovery import discover
from .models import Criteria, DiscoveryResult
from .ranking import rank


router = APIRouter(prefix="/discovery", tags=["discovery"])


def _require_internal_access(token: str | None) -> None:
    expected = (os.environ.get("DISCOVERY_ADMIN_TOKEN") or "").strip()
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Discovery engine is internal-only.")


@router.post("/find", response_model=DiscoveryResult)
def find_channels(
    criteria: Criteria,
    x_discovery_admin_token: str | None = Header(default=None, alias="X-Discovery-Admin-Token"),
):
    _require_internal_access(x_discovery_admin_token)
    result = discover(criteria)
    result.channels = rank(result.channels, criteria)
    return result

