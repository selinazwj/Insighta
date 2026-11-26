"""Features / analytics / events."""

from fastapi import Request
from sqlalchemy.orm import Session
from typing import Any
from typing import Optional
from app.models import User
from app.models import UserEvent


def _client_ip(request: Request) -> Optional[str]:
    from app.features.trust.risk import _privacy_ref

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return _privacy_ref("analytics_network", forwarded.split(",")[0].strip())
    if request.client:
        return _privacy_ref("analytics_network", request.client.host)
    return None


def _safe_event_metadata(metadata: Any) -> dict:
    if not isinstance(metadata, dict):
        return {}
    allowed = {}
    for key, value in metadata.items():
        if key in {"password", "token", "verification_code", "answers", "content"}:
            continue
        safe_key = str(key)[:80]
        if isinstance(value, (str, int, float, bool)) or value is None:
            allowed[safe_key] = value if not isinstance(value, str) else value[:500]
        elif isinstance(value, list):
            allowed[safe_key] = [str(item)[:120] for item in value[:20]]
        elif isinstance(value, dict):
            allowed[safe_key] = {str(k)[:80]: str(v)[:200] for k, v in list(value.items())[:20]}
        else:
            allowed[safe_key] = str(value)[:200]
    return allowed


RESEARCHER_EVENT_NAMES = frozenset({
    "study_created", "study_published", "participant_approved",
    "participant_rejected", "study_shared",
})


VIEW_EVENT_NAMES = frozenset({
    "listing_view", "listing_viewed", "study_impression", "study_card_viewed",
    "page_view", "page_viewed",
})


START_EVENT_NAMES = frozenset({"study_start", "survey_started"})


CLICK_EVENT_NAMES = frozenset({"study_click"})


COMPLETE_EVENT_NAMES = frozenset({"study_complete", "survey_completed"})


def _event_source_from_request(request: Request, fallback: str = "") -> str:
    referer = (request.headers.get("referer") or "").lower()
    if "my-studies" in referer:
        return "My Studies"
    if "/dashboard" in referer:
        return "Dashboard"
    if "/r/" in referer:
        return "Study Page"
    return fallback or "—"


def _admin_event_role(event: UserEvent, user: Optional[User], metadata: dict) -> str:
    role = (metadata.get("user_role") or metadata.get("role") or "").strip().lower()
    if role == "participant":
        return "Participant"
    if role == "researcher":
        return "Researcher"
    if user and getattr(user, "welcome_email_role", None):
        welcome_role = (user.welcome_email_role or "").strip().lower()
        if welcome_role == "participant":
            return "Participant"
        if welcome_role == "researcher":
            return "Researcher"
    if event.event_name in RESEARCHER_EVENT_NAMES:
        return "Researcher"
    if event.user_id or event.anonymous_id:
        return "Participant"
    return "—"


def _admin_event_source(metadata: dict) -> str:
    source = (metadata.get("source") or metadata.get("surface") or "").strip()
    if not source:
        return "—"
    return source.replace("_", " ").title()


def _admin_event_details(metadata: dict) -> str:
    if not metadata:
        return "—"
    pieces = []
    if metadata.get("match_score") is not None:
        pieces.append(f"Match score: {metadata['match_score']}")
    if metadata.get("position") is not None:
        pieces.append(f"Position: {metadata['position']}")
    if metadata.get("reward_amount") is not None:
        try:
            pieces.append(f"Reward: ${float(metadata['reward_amount']):.2f}")
        except (TypeError, ValueError):
            pieces.append(f"Reward: {metadata['reward_amount']}")
    if metadata.get("button"):
        pieces.append(f"Button: {metadata['button']}")
    if not pieces:
        extras = []
        for key in ("referrer", "href"):
            if metadata.get(key):
                extras.append(str(metadata[key])[:120])
        if extras:
            return " · ".join(extras[:3])
        return "—"
    return ", ".join(pieces[:4])


def _record_user_event(
    db: Session,
    request: Request,
    event_name: str,
    user: Optional[User] = None,
    user_id: Optional[int] = None,
    anonymous_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[Any] = None,
    page_path: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[UserEvent]:
    clean_name = (event_name or "").strip().lower()[:80]
    if not clean_name:
        return None
    resolved_user_id = user.id if user else user_id
    path = (page_path or request.url.path)[:500]
    if request.url.query and not page_path:
        path = f"{path}?{request.url.query}"[:500]
    event = UserEvent(
        user_id=resolved_user_id,
        anonymous_id=(anonymous_id or "")[:120] or None,
        event_name=clean_name,
        target_type=(target_type or "")[:80] or None,
        target_id=str(target_id)[:120] if target_id is not None else None,
        page_path=path,
        metadata_json=_safe_event_metadata(metadata or {}),
        user_agent=(request.headers.get("user-agent") or "")[:800] or None,
        client_ip=_client_ip(request),
    )
    db.add(event)
    return event


__all__ = ['_client_ip', '_safe_event_metadata', 'RESEARCHER_EVENT_NAMES', 'VIEW_EVENT_NAMES', 'START_EVENT_NAMES', 'CLICK_EVENT_NAMES', 'COMPLETE_EVENT_NAMES', '_event_source_from_request', '_admin_event_role', '_admin_event_source', '_admin_event_details', '_record_user_event']
