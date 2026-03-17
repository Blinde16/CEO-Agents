from __future__ import annotations


def list_events(client_id: str) -> dict:
    return {"provider": "calendar", "client_id": client_id, "events": []}


def create_or_update_event(payload: dict) -> dict:
    return {"provider": "calendar", "status": "queued", "payload": payload}
