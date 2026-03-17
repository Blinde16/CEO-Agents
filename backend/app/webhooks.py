"""
n8n webhook endpoints — Layer 3 push delivery.

n8n calls these endpoints on a schedule (morning briefing, pre-meeting prep,
inbox triage).  Each endpoint does the AI work and returns structured JSON
that n8n then routes to Slack, Gmail, or any other delivery channel.

Authentication: set N8N_WEBHOOK_SECRET in both this service and n8n's HTTP
Request nodes.  Requests without the correct X-N8N-Secret header are rejected
when the secret is configured.

Endpoint summary:
  POST /webhooks/n8n/morning-briefing   — Daily digest + next meeting brief
  POST /webhooks/n8n/pre-meeting        — Brief for events starting in ≤35 min
  POST /webhooks/n8n/inbox-triage       — Prioritised inbox summary per client
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException

from app.database import db
from app.integrations import calendar, email
from app.llm import generate_briefing, triage_inbox
from app.settings import get_settings

router = APIRouter(prefix="/webhooks/n8n", tags=["n8n webhooks"])


def _verify_secret(x_n8n_secret: str | None) -> None:
    """Reject requests that don't carry the configured shared secret."""
    secret = get_settings().n8n_webhook_secret
    if secret and x_n8n_secret != secret:
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def _parse_event_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


async def _build_briefing_payload(client_id: str) -> dict | None:
    """
    Fetch the next upcoming event for a client, pull relevant emails, and
    generate a briefing.  Returns None if no events or Google not connected.
    """
    if not db.get_integration(client_id, "google"):
        return None

    client = db.get_client(client_id)
    if not client:
        return None

    events_result = calendar.list_events(client_id)
    upcoming = [e for e in events_result.get("events", []) if e.get("start")]
    if not upcoming:
        return None

    event = upcoming[0]
    attendee_emails = [a for a in event.get("attendees", []) if a]

    recent_emails: list[dict] = []
    for ae in attendee_emails[:3]:
        name_hint = ae.split("@")[0].replace(".", " ")
        msg = email.find_message_for_contact(client_id, name_hint)
        if msg:
            recent_emails.append(msg)

    briefing_data = await generate_briefing(client, event, recent_emails)

    return {
        "client_id": client_id,
        "client_name": client.display_name or client_id,
        "event_id": event.get("id", ""),
        "event_title": event.get("title", "Meeting"),
        "start_time": str(event.get("start", "")),
        "attendees": attendee_emails,
        "relationship_context": briefing_data.get("relationship_context", ""),
        "open_items": briefing_data.get("open_items", []),
        "suggested_talking_points": briefing_data.get("suggested_talking_points", []),
        "recent_emails": [
            {
                "from": e.get("from", ""),
                "subject": e.get("subject", ""),
                "snippet": e.get("snippet", ""),
            }
            for e in recent_emails
        ],
    }


# ---------------------------------------------------------------------------
# Morning briefing — called by n8n on a morning schedule (e.g. 7:00 AM)
# ---------------------------------------------------------------------------

@router.post("/morning-briefing")
async def morning_briefing(x_n8n_secret: str | None = Header(None)) -> dict:
    """
    Generate the morning briefing for all connected clients.

    n8n should:
      1. Call this endpoint at the start of each business day.
      2. Iterate the returned `briefings` array.
      3. For each item, send the briefing to the client's Slack DM or email.

    Returns:
      {
        "generated_at": "...",
        "briefings": [ { client_id, event_title, start_time, ... }, ... ]
      }
    """
    _verify_secret(x_n8n_secret)

    briefings = []
    for client in db.list_clients():
        payload = await _build_briefing_payload(client.client_id)
        if payload:
            briefings.append(payload)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "briefings": briefings,
        "count": len(briefings),
    }


# ---------------------------------------------------------------------------
# Pre-meeting brief — called by n8n every 5 minutes
# ---------------------------------------------------------------------------

@router.post("/pre-meeting")
async def pre_meeting_brief(x_n8n_secret: str | None = Header(None)) -> dict:
    """
    Detect events starting within the next 30–35 minutes for all clients and
    return a briefing for each one.

    n8n should:
      1. Poll this endpoint every 5 minutes.
      2. If `briefings` is non-empty, send each briefing to the matching
         client (Slack DM, push notification, etc.).

    Returns:
      {
        "checked_at": "...",
        "briefings": [ { client_id, event_title, minutes_until_start, ... } ]
      }
    """
    _verify_secret(x_n8n_secret)

    now = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=25)
    window_end = now + timedelta(minutes=40)

    briefings = []
    for client in db.list_clients():
        if not db.get_integration(client.client_id, "google"):
            continue

        events_result = calendar.list_events(client.client_id)
        for event in events_result.get("events", []):
            start_dt = _parse_event_dt(event.get("start"))
            if start_dt is None:
                continue
            if not (window_start <= start_dt <= window_end):
                continue

            attendee_emails = [a for a in event.get("attendees", []) if a]
            recent_emails: list[dict] = []
            for ae in attendee_emails[:3]:
                name_hint = ae.split("@")[0].replace(".", " ")
                msg = email.find_message_for_contact(client.client_id, name_hint)
                if msg:
                    recent_emails.append(msg)

            briefing_data = await generate_briefing(client, event, recent_emails)
            minutes_until = int((start_dt - now).total_seconds() / 60)

            briefings.append({
                "client_id": client.client_id,
                "client_name": client.display_name or client.client_id,
                "event_id": event.get("id", ""),
                "event_title": event.get("title", "Meeting"),
                "start_time": start_dt.isoformat(),
                "minutes_until_start": minutes_until,
                "attendees": attendee_emails,
                "relationship_context": briefing_data.get("relationship_context", ""),
                "open_items": briefing_data.get("open_items", []),
                "suggested_talking_points": briefing_data.get("suggested_talking_points", []),
            })

    return {
        "checked_at": now.isoformat(),
        "briefings": briefings,
        "count": len(briefings),
    }


# ---------------------------------------------------------------------------
# Inbox triage — called by n8n on an hourly schedule
# ---------------------------------------------------------------------------

@router.post("/inbox-triage")
async def inbox_triage(x_n8n_secret: str | None = Header(None)) -> dict:
    """
    Run prioritised inbox triage for all connected clients.

    n8n should:
      1. Call this endpoint every hour (or at a configured interval).
      2. For each client result, post urgent/action-required items to Slack.

    Returns:
      {
        "triaged_at": "...",
        "results": [
          {
            "client_id": "...",
            "urgent_count": N,
            "action_required_count": N,
            "meeting_requests": N,
            "items": [ { subject, sender, urgency_score, summary, ... } ]
          }
        ]
      }
    """
    _verify_secret(x_n8n_secret)

    results = []
    for client in db.list_clients():
        if not db.get_integration(client.client_id, "google"):
            continue

        messages_result = email.list_messages(client.client_id)
        messages = messages_result.get("messages", [])
        if not messages:
            continue

        triage_items = await triage_inbox(client, messages)
        if not triage_items:
            continue

        urgent = [r for r in triage_items if r.urgency_score >= 4]
        action_required = [r for r in triage_items if r.requires_reply and r.urgency_score < 4]
        meeting_requests = [
            r for r in triage_items
            if r.category == "meeting_request" and r.proposed_meeting_time
        ]

        results.append({
            "client_id": client.client_id,
            "client_name": client.display_name or client.client_id,
            "urgent_count": len(urgent),
            "action_required_count": len(action_required),
            "meeting_request_count": len(meeting_requests),
            "items": [
                {
                    "message_id": r.message_id,
                    "subject": r.subject,
                    "sender": r.sender,
                    "category": r.category,
                    "urgency_score": r.urgency_score,
                    "summary": r.summary,
                    "action_items": r.action_items,
                    "requires_reply": r.requires_reply,
                    "reply_deadline": r.reply_deadline,
                    "proposed_meeting_time": r.proposed_meeting_time,
                }
                for r in triage_items[:10]
            ],
        })

    return {
        "triaged_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "client_count": len(results),
    }
