from __future__ import annotations

import base64
import quopri
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx

from app.settings import get_settings

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/contacts.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


class GoogleIntegrationConfigError(ValueError):
    pass


def build_auth_url(client_id: str, state: str) -> str:
    settings = get_settings()
    if not settings.google_client_id:
        raise GoogleIntegrationConfigError("GOOGLE_CLIENT_ID is not configured")

    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "scope": " ".join(GOOGLE_SCOPES),
            "state": state,
            "login_hint": client_id,
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


async def exchange_code_for_tokens(code: str) -> dict:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise GoogleIntegrationConfigError("Google OAuth credentials are not fully configured")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        return response.json()


async def fetch_user_info(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise GoogleIntegrationConfigError("Google OAuth credentials are not fully configured")

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        return response.json()


def ensure_valid_access_token(token_payload: dict) -> dict:
    expires_at = token_payload.get("expires_at")
    refresh_token = token_payload.get("refresh_token")

    if expires_at:
        try:
            expires_at_dt = datetime.fromisoformat(expires_at)
            if expires_at_dt.tzinfo is None:
                expires_at_dt = expires_at_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            expires_at_dt = datetime.now(timezone.utc) - timedelta(minutes=1)
    else:
        expires_at_dt = datetime.now(timezone.utc) - timedelta(minutes=1)

    if token_payload.get("access_token") and expires_at_dt > datetime.now(timezone.utc) + timedelta(minutes=1):
        return token_payload

    if not refresh_token:
        raise GoogleIntegrationConfigError("Google refresh token is missing. Reconnect Google to continue.")

    refreshed = refresh_access_token(refresh_token)
    updated = {
        **token_payload,
        **refreshed,
        "refresh_token": refresh_token,
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
        ).isoformat(),
    }
    return updated


def get_email_body(token_payload: dict, message_id: str) -> dict:
    """Fetch the full plain-text body of a single Gmail message."""
    token_payload = ensure_valid_access_token(token_payload)
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            params={"format": "full"},
        )
        response.raise_for_status()
        msg_data = response.json()

    body_text = _extract_body_text(msg_data.get("payload", {}))
    headers = {
        h.get("name", "").lower(): h.get("value", "")
        for h in msg_data.get("payload", {}).get("headers", [])
    }
    return {
        "id": msg_data.get("id"),
        "thread_id": msg_data.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": headers.get("date", ""),
        "body": body_text[:3000],  # cap at 3000 chars to control token cost
        "snippet": msg_data.get("snippet", ""),
        "token_payload": token_payload,
    }


def get_email_thread(token_payload: dict, thread_id: str, max_messages: int = 4) -> dict:
    """Fetch the last N messages in a Gmail thread for context-aware replies."""
    token_payload = ensure_valid_access_token(token_payload)
    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            params={"format": "full"},
        )
        response.raise_for_status()
        thread_data = response.json()

    messages = thread_data.get("messages", [])[-max_messages:]
    thread_messages = []
    for msg in messages:
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in msg.get("payload", {}).get("headers", [])
        }
        body = _extract_body_text(msg.get("payload", {}))
        thread_messages.append({
            "id": msg.get("id"),
            "from": headers.get("from", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", ""),
            "body": body[:1500],  # cap each message at 1500 chars
        })

    return {
        "thread_id": thread_id,
        "messages": thread_messages,
        "token_payload": token_payload,
    }


def lookup_contact_email(token_payload: dict, name: str) -> str | None:
    """Look up a contact's email address from Google Contacts (People API)."""
    token_payload = ensure_valid_access_token(token_payload)
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                "https://people.googleapis.com/v1/people:searchContacts",
                headers={"Authorization": f"Bearer {token_payload['access_token']}"},
                params={
                    "query": name,
                    "readMask": "names,emailAddresses",
                    "pageSize": 3,
                },
            )
            if response.status_code != 200:
                return None
            data = response.json()
    except Exception:
        return None

    for result in data.get("results", []):
        person = result.get("person", {})
        emails = person.get("emailAddresses", [])
        if emails:
            return emails[0].get("value")
    return None


def create_gmail_draft(token_payload: dict, payload: dict) -> dict:
    token_payload = ensure_valid_access_token(token_payload)
    recipient_email = payload.get("recipient_email")
    subject = payload.get("subject") or f"Re: {payload.get('topic', 'follow-up')}".strip()
    body = payload.get("draft_body") or payload.get("source_text") or ""

    email_message = EmailMessage()
    email_message["To"] = recipient_email
    email_message["Subject"] = subject
    email_message.set_content(body)

    # Thread the reply if we have a thread_id
    thread_id = payload.get("thread_id")
    encoded_message = base64.urlsafe_b64encode(email_message.as_bytes()).decode("utf-8")

    draft_body: dict = {"message": {"raw": encoded_message}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            json=draft_body,
        )
        response.raise_for_status()
        body_json = response.json()

    return {
        "provider": "gmail",
        "status": "drafted",
        "draft_id": body_json.get("id"),
        "message_id": body_json.get("message", {}).get("id"),
        "subject": subject,
        "body": body,
        "recipient_email": recipient_email,
        "token_payload": token_payload,
    }


def list_gmail_messages(token_payload: dict, max_results: int = 10, query: str | None = None) -> dict:
    token_payload = ensure_valid_access_token(token_payload)

    params: dict = {"maxResults": max_results, "labelIds": ["INBOX"]}
    if query:
        params["q"] = query

    with httpx.Client(timeout=20.0) as client:
        list_response = client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            params=params,
        )
        list_response.raise_for_status()
        message_refs = list_response.json().get("messages", [])

        messages = []
        for ref in message_refs[:max_results]:
            detail_response = client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{ref['id']}",
                headers={"Authorization": f"Bearer {token_payload['access_token']}"},
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
            detail_response.raise_for_status()
            body_json = detail_response.json()
            headers = {
                header.get("name", "").lower(): header.get("value", "")
                for header in body_json.get("payload", {}).get("headers", [])
            }
            messages.append(
                {
                    "id": body_json.get("id"),
                    "thread_id": body_json.get("threadId"),
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", "(no subject)"),
                    "date": headers.get("date", ""),
                    "snippet": body_json.get("snippet", ""),
                }
            )

    return {
        "provider": "gmail",
        "status": "listed",
        "messages": messages,
        "token_payload": token_payload,
    }


def create_calendar_event(token_payload: dict, payload: dict, operation: str) -> dict:
    token_payload = ensure_valid_access_token(token_payload)

    requested_time = str(payload.get("requested_time", ""))
    start_dt = _coerce_requested_time(requested_time)
    duration_minutes = int(payload.get("duration_minutes", 30))
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    attendees = [{"email": attendee} for attendee in _attendee_emails(payload.get("attendees", []))]
    event_body = {
        "summary": payload.get("title", "Executive meeting"),
        "description": payload.get("source_text", ""),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
        "attendees": attendees,
    }

    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            json=event_body,
        )
        response.raise_for_status()
        body_json = response.json()

    return {
        "provider": "google_calendar",
        "status": operation,
        "event_id": body_json.get("id"),
        "html_link": body_json.get("htmlLink"),
        "title": body_json.get("summary"),
        "requested_time": requested_time,
        "duration_minutes": duration_minutes,
        "attendees": [attendee.get("email") for attendee in body_json.get("attendees", [])],
        "token_payload": token_payload,
    }


def update_calendar_event(token_payload: dict, payload: dict, operation: str) -> dict:
    token_payload = ensure_valid_access_token(token_payload)
    event = _find_matching_event(token_payload, payload)

    requested_time = str(payload.get("requested_time", ""))
    start_dt = _coerce_requested_time(requested_time)
    duration_minutes = int(payload.get("duration_minutes", 30))
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    updated_body = {
        "summary": payload.get("title") or event.get("summary") or "Executive meeting",
        "description": payload.get("source_text") or event.get("description") or "",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
        "attendees": event.get("attendees", []),
    }

    with httpx.Client(timeout=20.0) as client:
        response = client.put(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event['id']}",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            json=updated_body,
        )
        response.raise_for_status()
        body_json = response.json()

    return {
        "provider": "google_calendar",
        "status": operation,
        "event_id": body_json.get("id"),
        "html_link": body_json.get("htmlLink"),
        "title": body_json.get("summary"),
        "requested_time": requested_time,
        "attendees": [attendee.get("email") for attendee in body_json.get("attendees", [])],
        "matched_on": _match_summary(payload),
        "token_payload": token_payload,
    }


def cancel_calendar_event(token_payload: dict, payload: dict, operation: str) -> dict:
    token_payload = ensure_valid_access_token(token_payload)
    event = _find_matching_event(token_payload, payload)

    with httpx.Client(timeout=20.0) as client:
        response = client.delete(
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event['id']}",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
        )
        response.raise_for_status()

    return {
        "provider": "google_calendar",
        "status": operation,
        "event_id": event.get("id"),
        "title": event.get("summary"),
        "requested_time": event.get("start", {}).get("dateTime") or event.get("start", {}).get("date"),
        "attendees": [attendee.get("email") for attendee in event.get("attendees", [])],
        "matched_on": _match_summary(payload),
        "token_payload": token_payload,
    }


def list_calendar_events(token_payload: dict, days: int = 7, max_results: int = 10) -> dict:
    token_payload = ensure_valid_access_token(token_payload)
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            params={
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": now.isoformat(),
                "timeMax": end.isoformat(),
                "maxResults": max_results,
            },
        )
        response.raise_for_status()
        body_json = response.json()

    items = [
        {
            "id": item.get("id"),
            "title": item.get("summary") or "Untitled event",
            "start": item.get("start", {}).get("dateTime") or item.get("start", {}).get("date"),
            "end": item.get("end", {}).get("dateTime") or item.get("end", {}).get("date"),
            "attendees": [attendee.get("email") for attendee in item.get("attendees", [])],
            "html_link": item.get("htmlLink"),
        }
        for item in body_json.get("items", [])
    ]

    return {
        "provider": "google_calendar",
        "status": "listed",
        "events": items,
        "token_payload": token_payload,
    }


def _extract_body_text(payload: dict) -> str:
    """Recursively extract plain text from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return _decode_base64url(body_data)

    # Prefer text/plain parts; fall back to text/html
    parts = payload.get("parts", [])
    plain_text = ""
    html_text = ""
    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            plain_text = _extract_body_text(part)
        elif part_mime == "text/html" and not plain_text:
            html_text = _extract_body_text(part)
        elif part_mime.startswith("multipart/"):
            sub = _extract_body_text(part)
            if sub:
                plain_text = sub

    return plain_text or html_text


def _decode_base64url(data: str) -> str:
    try:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _coerce_requested_time(requested_time: str) -> datetime:
    now = datetime.now(timezone.utc)
    lowered = requested_time.lower().strip()

    if lowered in ("tomorrow", "tomorrow morning"):
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if lowered == "tomorrow afternoon":
        return (now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    if lowered in ("next week", "early next week"):
        return (now + timedelta(days=7)).replace(hour=9, minute=0, second=0, microsecond=0)
    if lowered == "this afternoon":
        return now.replace(hour=14, minute=0, second=0, microsecond=0)
    if lowered == "this morning":
        return now.replace(hour=9, minute=0, second=0, microsecond=0)

    try:
        parsed = datetime.fromisoformat(requested_time.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)


def _attendee_emails(attendees: object) -> list[str]:
    if not isinstance(attendees, list):
        return []

    emails: list[str] = []
    for attendee in attendees:
        text = str(attendee).strip()
        if not text:
            continue
        if "@" in text:
            emails.append(text)
        else:
            # Placeholder — real email resolution happens via lookup_contact_email before this point
            emails.append(f"{text.lower().replace(' ', '.')}@example.com")
    return emails


def _find_matching_event(token_payload: dict, payload: dict) -> dict:
    candidates = _list_upcoming_events(token_payload, payload)
    exact_matches = [event for event in candidates if _event_matches_payload(event, payload)]

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise GoogleIntegrationConfigError(
            "I found multiple matching calendar events. Please be more specific about the meeting title or time."
        )
    raise GoogleIntegrationConfigError(
        "I could not find a matching calendar event to update. Try mentioning the meeting title or attendee."
    )


def _list_upcoming_events(token_payload: dict, payload: dict) -> list[dict]:
    query = _match_summary(payload)
    params: dict = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": datetime.now(timezone.utc).isoformat(),
        "maxResults": 20,
    }
    if query:
        params["q"] = query

    with httpx.Client(timeout=20.0) as client:
        response = client.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers={"Authorization": f"Bearer {token_payload['access_token']}"},
            params=params,
        )
        response.raise_for_status()
        return response.json().get("items", [])


def _event_matches_payload(event: dict, payload: dict) -> bool:
    target_title = str(payload.get("title", "")).strip().lower()
    target_contact = str(payload.get("contact_name", "")).strip().lower()
    target_time = str(payload.get("requested_time", "")).strip().lower()

    summary = str(event.get("summary", "")).strip().lower()
    attendees = " ".join(attendee.get("email", "").lower() for attendee in event.get("attendees", []))
    start_text = str(event.get("start", {}).get("dateTime") or event.get("start", {}).get("date") or "").lower()

    title_match = not target_title or target_title in summary
    contact_match = not target_contact or target_contact.replace(" ", ".") in attendees or target_contact in summary
    time_match = not target_time or target_time in {"tomorrow", "next week"} or target_time in start_text
    return title_match and contact_match and time_match


def _match_summary(payload: dict) -> str:
    return str(payload.get("title") or payload.get("contact_name") or payload.get("source_text") or "").strip()
