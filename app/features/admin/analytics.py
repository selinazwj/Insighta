"""Features / admin / analytics."""

from datetime import datetime
from datetime import timedelta
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Any
from typing import Optional
from app.database import get_db
from app.features.analytics.events import COMPLETE_EVENT_NAMES
from app.features.analytics.events import START_EVENT_NAMES
from app.features.analytics.events import VIEW_EVENT_NAMES
from app.models import Response
from app.models import Survey
from app.models import User
from app.models import UserEvent


async def admin_analytics(
    request: Request,
    admin_key: str = Query(None),
    days: int = Query(30),
    db: Session = Depends(get_db),
):
    from app.features.admin.access import _admin_key_matches

    if not _admin_key_matches(admin_key):
        raise HTTPException(403, "Unauthorized")
    days = max(1, min(days, 180))
    since = datetime.utcnow() - timedelta(days=days)

    events = db.query(UserEvent).filter(UserEvent.created_at >= since).order_by(UserEvent.created_at.desc()).limit(20000).all()
    event_counts: dict[str, int] = {}
    unique_people = set()
    for event in events:
        event_counts[event.event_name] = event_counts.get(event.event_name, 0) + 1
        if event.user_id:
            unique_people.add(f"u:{event.user_id}")
        elif event.anonymous_id:
            unique_people.add(f"a:{event.anonymous_id}")

    def in_analytics_window(value) -> bool:
        if not value:
            return False
        if getattr(value, "tzinfo", None):
            value = value.replace(tzinfo=None)
        return value >= since

    def response_counts_in_window(response: Response) -> tuple[bool, bool]:
        started_in_window = in_analytics_window(response.started_at)
        # Older rows may not have timestamps even though their status is useful.
        if not response.started_at:
            started_in_window = response.status in {"started", "completed"}
        completed_in_window = in_analytics_window(response.completed_at)
        if not response.completed_at and response.status == "completed":
            completed_in_window = started_in_window
        return started_in_window, completed_in_window

    all_response_rows = db.query(Response).all()
    response_started_total = 0
    completed_total = 0
    for response in all_response_rows:
        started_in_window, completed_in_window = response_counts_in_window(response)
        if started_in_window:
            response_started_total += 1
        if completed_in_window:
            completed_total += 1
    starts_total = max(event_counts.get("study_start", 0) + event_counts.get("survey_started", 0), response_started_total)
    start_to_complete = round((completed_total / starts_total) * 100, 1) if starts_total else 0

    surveys = db.query(Survey).order_by(Survey.created_at.desc()).limit(200).all()
    survey_ids = [s.id for s in surveys]
    response_rows = []
    if survey_ids:
        response_rows = db.query(Response).filter(Response.survey_id.in_(survey_ids)).all()

    event_by_survey: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.target_type != "survey" or not event.target_id:
            continue
        bucket = event_by_survey.setdefault(event.target_id, {
            "views": 0,
            "unique_viewers": set(),
            "starts": 0,
            "unique_starters": set(),
            "completed": 0,
        })
        actor = f"u:{event.user_id}" if event.user_id else f"a:{event.anonymous_id or event.client_ip or event.id}"
        if event.event_name in VIEW_EVENT_NAMES:
            bucket["views"] += 1
            bucket["unique_viewers"].add(actor)
        elif event.event_name in START_EVENT_NAMES:
            bucket["starts"] += 1
            bucket["unique_starters"].add(actor)
        elif event.event_name in COMPLETE_EVENT_NAMES:
            bucket["completed"] += 1

    responses_by_survey: dict[int, dict[str, int]] = {}

    for response in response_rows:
        bucket = responses_by_survey.setdefault(response.survey_id, {"responses": 0, "completed": 0})
        started_in_window, completed_in_window = response_counts_in_window(response)
        if started_in_window:
            bucket["responses"] += 1
        if completed_in_window:
            bucket["completed"] += 1

    listing_funnel = []
    for survey in surveys:
        event_bucket = event_by_survey.get(str(survey.id), {})
        response_bucket = responses_by_survey.get(survey.id, {"responses": 0, "completed": 0})
        tracked_views = int(event_bucket.get("views", 0) or 0)
        starts = max(int(event_bucket.get("starts", 0) or 0), int(response_bucket.get("responses", 0) or 0))
        completed = max(int(event_bucket.get("completed", 0) or 0), int(response_bucket.get("completed", 0) or 0))
        views = max(tracked_views, starts)
        unique_viewers = max(len(event_bucket.get("unique_viewers", set())), len(event_bucket.get("unique_starters", set())))
        listing_funnel.append({
            "id": survey.id,
            "title": survey.title,
            "status": survey.status,
            "views": views,
            "tracked_views": tracked_views,
            "unique_viewers": unique_viewers,
            "starts": starts,
            "unique_starters": len(event_bucket.get("unique_starters", set())),
            "completed": completed,
            "start_rate": round((starts / views) * 100, 1) if views else None,
            "completion_rate": round((completed / starts) * 100, 1) if starts else None,
            "created_at": survey.created_at.isoformat() if survey.created_at else None,
        })
    listing_funnel.sort(key=lambda item: (item["views"], item["starts"], item["completed"], item["id"]), reverse=True)

    recent = events[:80]
    user_ids = {event.user_id for event in recent if event.user_id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    recent_survey_ids = []
    for event in recent:
        if event.target_type == "survey" and event.target_id and str(event.target_id).isdigit():
            recent_survey_ids.append(int(event.target_id))
    survey_map = {s.id: s for s in db.query(Survey).filter(Survey.id.in_(set(recent_survey_ids))).all()} if recent_survey_ids else {}
    recent_activity = []
    for event in recent:
        user = users.get(event.user_id)
        survey = survey_map.get(int(event.target_id)) if event.target_type == "survey" and event.target_id and str(event.target_id).isdigit() else None
        guest_suffix = (event.anonymous_id or "")[-4:].upper()
        actor_label = user.email if user else (f"Guest visitor #{guest_suffix}" if guest_suffix else "Guest visitor")
        recent_activity.append({
            "id": event.id,
            "event_name": event.event_name,
            "user": actor_label,
            "actor_type": "user" if user else "guest",
            "anonymous_id": event.anonymous_id,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "target_label": survey.title if survey else None,
            "page_path": event.page_path,
            "metadata": event.metadata_json or {},
            "created_at": event.created_at.isoformat() if event.created_at else None,
        })

    return JSONResponse({
        "overview": {
            "days": days,
            "total_events": len(events),
            "unique_people": len(unique_people),
            "listing_views": (
                event_counts.get("listing_view", 0)
                + event_counts.get("listing_viewed", 0)
                + event_counts.get("study_impression", 0)
                + event_counts.get("study_card_viewed", 0)
            ),
            "start_clicks": starts_total,
            "completed_surveys": completed_total,
            "start_to_complete_rate": start_to_complete,
        },
        "event_counts": event_counts,
        "listing_funnel": listing_funnel[:100],
        "recent_activity": recent_activity,
    })


def _admin_event_actor(event: UserEvent, users: dict[int, User], anonymous_user_ids: Optional[dict[str, int]] = None) -> dict[str, str]:
    inferred_user_id = None
    if not event.user_id and event.anonymous_id and anonymous_user_ids:
        inferred_user_id = anonymous_user_ids.get(event.anonymous_id)
    user = users.get(event.user_id or inferred_user_id)
    if user:
        label = user.email or user.username or f"User #{user.id}"
        return {"label": label, "type": "user", "email": user.email or "", "username": user.username or ""}
    guest_suffix = (event.anonymous_id or event.client_ip or str(event.id or ""))[-4:].upper()
    label = f"Guest visitor #{guest_suffix}" if guest_suffix else "Guest visitor"
    return {"label": label, "type": "guest", "email": "", "username": ""}


def _admin_event_payload(event: UserEvent, users: dict[int, User], survey_map: dict[int, Survey], anonymous_user_ids: Optional[dict[str, int]] = None) -> dict[str, Any]:
    from app.features.analytics.events import _admin_event_details
    from app.features.analytics.events import _admin_event_role
    from app.features.analytics.events import _admin_event_source

    survey = survey_map.get(int(event.target_id)) if event.target_type == "survey" and event.target_id and str(event.target_id).isdigit() else None
    inferred_user_id = anonymous_user_ids.get(event.anonymous_id) if anonymous_user_ids and event.anonymous_id else None
    actor = _admin_event_actor(event, users, anonymous_user_ids)
    metadata = event.metadata_json or {}
    user = users.get(event.user_id or inferred_user_id)
    return {
        "id": event.id,
        "event_name": event.event_name,
        "user": actor["label"],
        "role": _admin_event_role(event, user, metadata),
        "actor_type": actor["type"],
        "user_email": actor["email"],
        "username": actor["username"],
        "user_id": event.user_id or inferred_user_id,
        "anonymous_id": event.anonymous_id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "target_label": survey.title if survey else (metadata.get("study_title") or None),
        "page_path": event.page_path,
        "source": _admin_event_source(metadata),
        "details": _admin_event_details(metadata),
        "metadata": metadata,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _anonymous_user_map(db: Session, anonymous_ids: set[str]) -> dict[str, int]:
    clean_ids = {value for value in anonymous_ids if value}
    if not clean_ids:
        return {}
    rows = db.query(UserEvent.anonymous_id, UserEvent.user_id).filter(
        UserEvent.anonymous_id.in_(clean_ids),
        UserEvent.user_id.isnot(None),
    ).order_by(UserEvent.created_at.desc()).limit(5000).all()
    result: dict[str, int] = {}
    for anonymous_id, user_id in rows:
        if anonymous_id and user_id and anonymous_id not in result:
            result[anonymous_id] = user_id
    return result


async def admin_activity(
    request: Request,
    admin_key: str = Query(None),
    survey_id: Optional[int] = Query(None),
    event_name: str = Query(""),
    q: str = Query(""),
    limit: int = Query(100),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    from app.features.admin.access import _admin_key_matches

    if not _admin_key_matches(admin_key):
        raise HTTPException(403, "Unauthorized")

    limit = max(20, min(limit, 500))
    offset = max(0, offset)
    event_name = (event_name or "").strip()
    q = (q or "").strip()

    surveys = db.query(Survey).order_by(Survey.created_at.desc()).limit(1000).all()
    survey_options = [{
        "id": survey.id,
        "title": survey.title,
        "status": survey.status,
        "created_at": survey.created_at.isoformat() if survey.created_at else None,
    } for survey in surveys]

    query = db.query(UserEvent)
    if survey_id:
        query = query.filter(UserEvent.target_type == "survey", UserEvent.target_id == str(survey_id))
    if event_name:
        query = query.filter(UserEvent.event_name == event_name)
    if q:
        like = f"%{q}%"
        matching_users = db.query(User.id).filter(or_(User.email.ilike(like), User.username.ilike(like))).limit(500).all()
        matching_user_ids = [row[0] for row in matching_users]
        conditions = [
            UserEvent.anonymous_id.ilike(like),
            UserEvent.client_ip.ilike(like),
            UserEvent.page_path.ilike(like),
        ]
        if matching_user_ids:
            conditions.append(UserEvent.user_id.in_(matching_user_ids))
        query = query.filter(or_(*conditions))

    total = query.count()
    events = query.order_by(UserEvent.created_at.desc()).offset(offset).limit(limit).all()

    anonymous_user_ids = _anonymous_user_map(db, {event.anonymous_id for event in events if event.anonymous_id})
    user_ids = {event.user_id for event in events if event.user_id}
    user_ids.update(anonymous_user_ids.values())
    survey_ids = {
        int(event.target_id)
        for event in events
        if event.target_type == "survey" and event.target_id and str(event.target_id).isdigit()
    }
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    survey_map = {s.id: s for s in db.query(Survey).filter(Survey.id.in_(survey_ids)).all()} if survey_ids else {}
    activity = [_admin_event_payload(event, users, survey_map, anonymous_user_ids) for event in events]

    people = []
    if survey_id:
        survey_events = db.query(UserEvent).filter(
            UserEvent.target_type == "survey",
            UserEvent.target_id == str(survey_id),
        ).order_by(UserEvent.created_at.asc()).limit(20000).all()
        survey_anonymous_user_ids = _anonymous_user_map(db, {event.anonymous_id for event in survey_events if event.anonymous_id})
        response_rows = db.query(Response).filter(Response.survey_id == survey_id).all()
        survey_user_ids = {event.user_id for event in survey_events if event.user_id}
        survey_user_ids.update(survey_anonymous_user_ids.values())
        survey_user_ids.update({response.participant_id for response in response_rows if response.participant_id})
        survey_users = {u.id: u for u in db.query(User).filter(User.id.in_(survey_user_ids)).all()} if survey_user_ids else {}
        grouped: dict[str, dict[str, Any]] = {}
        view_events = VIEW_EVENT_NAMES
        start_events = START_EVENT_NAMES
        complete_events = COMPLETE_EVENT_NAMES
        for event in survey_events:
            inferred_user_id = survey_anonymous_user_ids.get(event.anonymous_id) if event.anonymous_id else None
            actor_key = f"u:{event.user_id or inferred_user_id}" if (event.user_id or inferred_user_id) else f"a:{event.anonymous_id or event.client_ip or event.id}"
            actor = _admin_event_actor(event, survey_users, survey_anonymous_user_ids)
            bucket = grouped.setdefault(actor_key, {
                "user": actor["label"],
                "actor_type": actor["type"],
                "user_email": actor["email"],
                "username": actor["username"],
                "user_id": event.user_id or inferred_user_id,
                "anonymous_id": event.anonymous_id,
                "first_opened_at": None,
                "first_started_at": None,
                "completed_at": None,
                "last_event_at": None,
                "event_count": 0,
                "last_event_name": None,
            })
            bucket["event_count"] += 1
            bucket["last_event_at"] = event.created_at.isoformat() if event.created_at else None
            bucket["last_event_name"] = event.event_name
            if event.event_name in view_events and not bucket["first_opened_at"]:
                bucket["first_opened_at"] = event.created_at.isoformat() if event.created_at else None
            if event.event_name in start_events and not bucket["first_started_at"]:
                bucket["first_started_at"] = event.created_at.isoformat() if event.created_at else None
            if event.event_name in complete_events and not bucket["completed_at"]:
                bucket["completed_at"] = event.created_at.isoformat() if event.created_at else None

        for response in response_rows:
            user = survey_users.get(response.participant_id)
            actor_key = f"u:{response.participant_id}" if response.participant_id else f"response:{response.id}"
            bucket = grouped.setdefault(actor_key, {
                "user": (user.email or user.username or f"User #{user.id}") if user else f"User #{response.participant_id}",
                "actor_type": "user",
                "user_email": user.email if user else "",
                "username": user.username if user else "",
                "user_id": response.participant_id,
                "anonymous_id": None,
                "first_opened_at": None,
                "first_started_at": None,
                "completed_at": None,
                "last_event_at": None,
                "event_count": 0,
                "last_event_name": None,
            })
            started_at = response.started_at.isoformat() if response.started_at else None
            completed_at = response.completed_at.isoformat() if response.completed_at else None
            if started_at and not bucket["first_started_at"]:
                bucket["first_started_at"] = started_at
            if completed_at and not bucket["completed_at"]:
                bucket["completed_at"] = completed_at
            latest_at = completed_at or started_at
            if latest_at and (not bucket["last_event_at"] or latest_at > bucket["last_event_at"]):
                bucket["last_event_at"] = latest_at
                bucket["last_event_name"] = "study_complete" if completed_at else "study_start"
            if response.id and bucket["event_count"] == 0:
                bucket["event_count"] = 1
        people = sorted(
            grouped.values(),
            key=lambda item: item.get("last_event_at") or "",
            reverse=True,
        )

    return JSONResponse({
        "surveys": survey_options,
        "events": activity,
        "people": people,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


__all__ = ['admin_analytics', '_admin_event_actor', '_admin_event_payload', '_anonymous_user_map', 'admin_activity']
