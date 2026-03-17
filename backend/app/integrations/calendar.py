from __future__ import annotations

from datetime import datetime, timezone

from app.integrations.google import (
    GoogleIntegrationConfigError,
    cancel_calendar_event,
    create_calendar_event,
    list_calendar_events,
    update_calendar_event,
)
from app.integrations.store import get_tokens, set_tokens

# ---------------------------------------------------------------------------
# Duration inference — smart defaults based on meeting type
# ---------------------------------------------------------------------------

_DURATION_KEYWORDS: list[tuple[list[str], int]] = [
    (["standup", "stand-up", "stand up", "daily sync", "scrum"], 15),
    (["coffee", "coffee chat", "intro call", "intro meeting", "introductory"], 30),
    (["lunch", "dinner"], 90),
    (["interview", "one on one", "1:1", "one-on-one"], 60),
    (["board", "investor", "fundraise", "fundraising", "pitch"], 90),
    (["workshop", "offsite", "planning session", "retreat"], 120),
    (["review", "debrief", "retrospective", "postmortem", "post-mortem"], 60),
    (["demo", "product demo", "presentation", "town hall", "all hands"], 60),
    (["call", "sync", "check-in", "check in", "catch up", "catchup"], 30),
    (["strategy", "roadmap", "planning", "quarterly"], 90),
]


def infer_duration_minutes(title: str, source_text: str = "") -> int:
    """Infer meeting duration from title/context. Returns minutes."""
    combined = f"{title} {source_text}".lower()
    for keywords, minutes in _DURATION_KEYWORDS:
        if any(kw in combined for kw in keywords):
            return minutes
    return 30  # default


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def check_conflicts(client_id: str, requested_time: str, duration_minutes: int) -> list[dict]:
    """
    Check for calendar conflicts at the requested time.
    Returns a list of conflicting event dicts (empty = no conflicts).
    """
    from app.integrations.google import _coerce_requested_time  # local import to avoid circular

    google_tokens = get_tokens(client_id, "google")
    if not google_tokens:
        return []  # can't check without connection

    try:
        result = list_calendar_events(google_tokens, days=14, max_results=50)
        updated_tokens = result.pop("token_payload", None)
        if isinstance(updated_tokens, dict):
            set_tokens(client_id, "google", updated_tokens)

        proposed_start = _coerce_requested_time(requested_time)
        from datetime import timedelta
        proposed_end = proposed_start + timedelta(minutes=duration_minutes)

        conflicts = []
        for event in result.get("events", []):
            event_start = _parse_event_dt(event.get("start"))
            event_end = _parse_event_dt(event.get("end"))
            if not event_start or not event_end:
                continue
            # Overlap: proposed starts before event ends AND proposed ends after event starts
            if proposed_start < event_end and proposed_end > event_start:
                conflicts.append(event)

        return conflicts
    except Exception:
        return []


def _parse_event_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            from datetime import timezone
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Focus block protection check
# ---------------------------------------------------------------------------

def check_focus_block_conflict(requested_time: str, focus_blocks: list[str]) -> str | None:
    """
    Returns a warning string if the requested_time falls inside a focus block,
    or None if it's clear.
    """
    from app.integrations.google import _coerce_requested_time
    if not focus_blocks:
        return None

    proposed = _coerce_requested_time(requested_time)
    proposed_hour = proposed.hour
    proposed_minute = proposed.minute

    for block in focus_blocks:
        try:
            start_raw, end_raw = block.split("-", 1)
            s_h, s_m = int(start_raw.split(":")[0]), int(start_raw.split(":")[1]) if ":" in start_raw else 0
            e_h, e_m = int(end_raw.split(":")[0]), int(end_raw.split(":")[1]) if ":" in end_raw else 0
            proposed_mins = proposed_hour * 60 + proposed_minute
            block_start_mins = s_h * 60 + s_m
            block_end_mins = e_h * 60 + e_m
            if block_start_mins <= proposed_mins < block_end_mins:
                return f"This time falls inside a protected focus block ({block}). Confirm to override."
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Standard CRUD operations
# ---------------------------------------------------------------------------

def list_events(client_id: str) -> dict:
    google_tokens = get_tokens(client_id, "google")
    if google_tokens:
        google_result = list_calendar_events(google_tokens)
        updated_tokens = google_result.pop("token_payload", None)
        if isinstance(updated_tokens, dict):
            set_tokens(client_id, "google", updated_tokens)
        return google_result
    return {"provider": "calendar", "client_id": client_id, "events": []}


def create_or_update_event(payload: dict, client_id: str | None = None, google_tokens: dict | None = None) -> dict:
    attendees = payload.get("attendees", [])
    title = payload.get("title", "Executive meeting")
    when = payload.get("requested_time", "TBD")
    operation = payload.get("operation", "scheduled")

    # Inject smart duration if not already set
    if not payload.get("duration_minutes"):
        payload = {**payload, "duration_minutes": infer_duration_minutes(title, str(payload.get("source_text", "")))}

    if google_tokens:
        try:
            if operation == "scheduled":
                google_result = create_calendar_event(google_tokens, payload, operation)
            elif operation == "rescheduled":
                google_result = update_calendar_event(google_tokens, payload, operation)
            elif operation == "cancelled":
                google_result = cancel_calendar_event(google_tokens, payload, operation)
            else:
                google_result = create_calendar_event(google_tokens, payload, operation)
            updated_tokens = google_result.pop("token_payload", None)
            if client_id and isinstance(updated_tokens, dict):
                set_tokens(client_id, "google", updated_tokens)
            return google_result
        except Exception as exc:
            if isinstance(exc, GoogleIntegrationConfigError):
                raise

    return {
        "provider": "calendar",
        "status": operation,
        "event_id": "evt-placeholder",
        "title": title,
        "requested_time": when,
        "duration_minutes": payload.get("duration_minutes", 30),
        "attendees": attendees,
    }


def get_briefing_context(client_id: str, attendee_emails: list[str]) -> list[dict]:
    """
    Fetch recent calendar events involving the given attendees for briefing context.
    Returns list of event dicts.
    """
    google_tokens = get_tokens(client_id, "google")
    if not google_tokens:
        return []
    try:
        result = list_calendar_events(google_tokens, days=30, max_results=20)
        updated_tokens = result.pop("token_payload", None)
        if isinstance(updated_tokens, dict):
            set_tokens(client_id, "google", updated_tokens)
        attendee_set = {e.lower() for e in attendee_emails}
        return [
            event for event in result.get("events", [])
            if any(a.lower() in attendee_set for a in event.get("attendees", []))
        ]
    except Exception:
        return []
