"""Features / admin / discovery."""

from fastapi import HTTPException
from fastapi import Request
from app.discovery.discovery import discover as discover_channels
from app.discovery.models import Criteria as DiscoveryCriteria
from app.discovery.ranking import rank as rank_discovery_channels


async def admin_find_discovery_channels(request: Request):
    from app.features.admin.access import _admin_key_matches

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")): raise HTTPException(403, "Unauthorized")
    try:
        criteria = DiscoveryCriteria(**(body.get("criteria") or {}))
    except Exception as exc:
        raise HTTPException(422, f"Invalid discovery criteria: {exc}")
    result = discover_channels(criteria)
    result.channels = rank_discovery_channels(result.channels, criteria)
    return result


__all__ = ['admin_find_discovery_channels']
