from __future__ import annotations

from app.integrations.google import (
    GoogleIntegrationConfigError,
    create_gmail_draft,
    get_email_body,
    get_email_thread,
    list_gmail_messages,
    lookup_contact_email,
)
from app.integrations.store import get_tokens, set_tokens


def draft_reply(payload: dict, client_id: str | None = None, google_tokens: dict | None = None) -> dict:
    if google_tokens:
        try:
            google_result = create_gmail_draft(google_tokens, payload)
            updated_tokens = google_result.pop("token_payload", None)
            if client_id and isinstance(updated_tokens, dict):
                set_tokens(client_id, "google", updated_tokens)
            return google_result
        except Exception as exc:
            if isinstance(exc, GoogleIntegrationConfigError):
                raise

    recipient = payload.get("recipient_name") or payload.get("recipient_email") or "recipient"
    source_text = payload.get("source_text", "").strip()
    topic = payload.get("topic", "follow-up")
    body = payload.get("draft_body") or (
        f"Hi {recipient},\n\n"
        f"This is a generated draft regarding {topic}.\n"
        f"Request context: {source_text or 'No additional context provided.'}\n\n"
        "Best,\nExecutive Office"
    )
    return {
        "provider": "email",
        "status": "drafted",
        "draft_id": "draft-placeholder",
        "subject": payload.get("subject") or f"Re: {topic.title()}",
        "body": body,
    }


def send_email(payload: dict) -> dict:
    return {"provider": "email", "status": "sent", "payload": payload}


def list_messages(client_id: str) -> dict:
    google_tokens = get_tokens(client_id, "google")
    if google_tokens:
        gmail_result = list_gmail_messages(google_tokens)
        updated_tokens = gmail_result.pop("token_payload", None)
        if isinstance(updated_tokens, dict):
            set_tokens(client_id, "google", updated_tokens)
        return gmail_result
    return {"provider": "email", "status": "listed", "messages": []}


def find_message_for_contact(client_id: str, contact_hint: str) -> dict | None:
    """
    Find the most relevant email for a contact. Returns full message body if available.
    Also resolves the contact's real email address from Google Contacts if possible.
    """
    google_tokens = get_tokens(client_id, "google")
    if not google_tokens:
        return None

    gmail_result = list_gmail_messages(google_tokens, max_results=10, query=contact_hint)
    updated_tokens = gmail_result.pop("token_payload", None)
    if isinstance(updated_tokens, dict):
        set_tokens(client_id, "google", updated_tokens)

    contact_hint_lower = contact_hint.lower()
    matched_message = None
    for message in gmail_result.get("messages", []):
        from_value = str(message.get("from", "")).lower()
        subject_value = str(message.get("subject", "")).lower()
        if contact_hint_lower in from_value or contact_hint_lower in subject_value:
            matched_message = message
            break
    if not matched_message:
        messages = gmail_result.get("messages", [])
        matched_message = messages[0] if messages else None

    if not matched_message:
        return None

    # Fetch full body if we have a message id
    current_tokens = get_tokens(client_id, "google")
    if current_tokens and matched_message.get("id"):
        try:
            full_msg = get_email_body(current_tokens, matched_message["id"])
            updated_tokens = full_msg.pop("token_payload", None)
            if isinstance(updated_tokens, dict):
                set_tokens(client_id, "google", updated_tokens)
            matched_message = {**matched_message, **full_msg}
        except Exception:
            pass  # fall back to snippet-only data

    return matched_message


def get_thread_for_message(client_id: str, thread_id: str) -> list[dict]:
    """Fetch the full thread for a given thread_id. Returns list of message dicts."""
    google_tokens = get_tokens(client_id, "google")
    if not google_tokens or not thread_id:
        return []

    try:
        result = get_email_thread(google_tokens, thread_id)
        updated_tokens = result.pop("token_payload", None)
        if isinstance(updated_tokens, dict):
            set_tokens(client_id, "google", updated_tokens)
        return result.get("messages", [])
    except Exception:
        return []


def resolve_contact_email(client_id: str, name: str) -> str | None:
    """Look up a contact's real email address from Google Contacts."""
    google_tokens = get_tokens(client_id, "google")
    if not google_tokens:
        return None
    try:
        return lookup_contact_email(google_tokens, name)
    except Exception:
        return None
