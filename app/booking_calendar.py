"""Calendar exports use stored slot dates, never the yearless booking label."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode


def booked_events(availability, labels):
    try:
        slots = json.loads(availability or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(slots, list):
        return []
    events = []
    for label in dict.fromkeys(labels):
        matches = [slot for slot in slots if isinstance(slot, dict) and slot.get("label") == label]
        # An ambiguous or incomplete date must never create a guessed appointment.
        if len(matches) != 1:
            return []
        slot = matches[0]
        try:
            start = datetime.fromisoformat(f"{slot['date']}T{slot['start']}")
            end = datetime.fromisoformat(f"{slot['date']}T{slot['end']}")
            if end <= start:
                end += timedelta(days=1)
            if slot.get("timezone"):
                zone = ZoneInfo(slot["timezone"])
                start = start.replace(tzinfo=zone).astimezone(timezone.utc)
                end = end.replace(tzinfo=zone).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError):
            return []
        events.append((label, start, end))
    return events


def google_calendar_links(title, events, location, study_url):
    links = []
    for index, (label, start, end) in enumerate(events, 1):
        def formatted(value):
            return value.strftime("%Y%m%dT%H%M%S") + ("Z" if value.tzinfo else "")
        details = f"{label}\n{location}\n{study_url}"
        if not start.tzinfo:
            details += "\nTime zone was not recorded. Confirm the session time zone with the researcher."
        links.append({
            "label": "Add to Google Calendar" + (f" · Session {index}" if len(events) > 1 else ""),
            "url": "https://calendar.google.com/calendar/render?" + urlencode({
                "action": "TEMPLATE", "text": title,
                "dates": f"{formatted(start)}/{formatted(end)}",
                "details": details, "location": location,
            }),
        })
    return links


def _text(value):
    return str(value or "").replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _fold(line):
    lines, current = [], ""
    for character in line:
        if len((current + character).encode("utf-8")) > 75:
            lines.append(current)
            current = " "
        current += character
    return "\r\n".join(lines + [current])


def calendar_document(response_id, title, events, location, study_url):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Insighta//Study bookings//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for label, start, end in events:
        identity = hashlib.sha256(label.encode()).hexdigest()[:24]
        def formatted(value):
            return value.strftime("%Y%m%dT%H%M%S") + ("Z" if value.tzinfo else "")
        description = f"{label}\n{location}\n{study_url}"
        if not start.tzinfo:
            description += "\nTime zone was not recorded. Confirm the session time zone with the researcher."
        lines.extend([
            "BEGIN:VEVENT", f"UID:booking-{response_id}-{identity}@insighta",
            f"DTSTAMP:{stamp}", f"DTSTART:{formatted(start)}", f"DTEND:{formatted(end)}",
            f"SUMMARY:{_text(title)}", f"LOCATION:{_text(location)}",
            f"DESCRIPTION:{_text(description)}", f"URL:{_text(study_url)}",
            "STATUS:CONFIRMED", "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
