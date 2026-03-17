from __future__ import annotations

from app.integrations import calendar, email, tasks


class UnsupportedActionError(ValueError):
    pass


def execute_action(action_type: str, payload: dict) -> dict:
    if action_type in {"create_event", "reschedule_event", "cancel_event"}:
        return calendar.create_or_update_event(payload)
    if action_type == "draft_email_reply":
        return email.draft_reply(payload)
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
