from __future__ import annotations

from app.integrations import calendar, email, tasks
from app.integrations.store import get_tokens


class UnsupportedActionError(ValueError):
    pass


def execute_action(client_id: str, action_type: str, payload: dict) -> dict:
    google_tokens = get_tokens(client_id, "google")
    if action_type in {"create_event", "reschedule_event", "cancel_event"}:
        operation = {
            "create_event": "scheduled",
            "reschedule_event": "rescheduled",
            "cancel_event": "cancelled",
        }[action_type]
        return calendar.create_or_update_event(
            {**payload, "operation": operation},
            client_id=client_id,
            google_tokens=google_tokens,
        )
    if action_type == "draft_email_reply":
        return email.draft_reply(payload, client_id=client_id, google_tokens=google_tokens)
    if action_type == "create_task":
        return tasks.create_task(payload)
    if action_type == "set_reminder":
        return tasks.set_reminder(payload)
    if action_type == "generate_daily_briefing":
        return {
            "schedule": [],
            "pending_tasks": [],
            "emails": [],
            "reminders": [],
        }
    raise UnsupportedActionError(f"Unsupported action type: {action_type}")
