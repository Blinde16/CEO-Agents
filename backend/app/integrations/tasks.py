from __future__ import annotations


def create_task(payload: dict) -> dict:
    return {"provider": "tasks", "task_id": "task-placeholder", "payload": payload}


def set_reminder(payload: dict) -> dict:
    return {"provider": "tasks", "reminder_id": "reminder-placeholder", "payload": payload}
