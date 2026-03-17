from __future__ import annotations


def draft_reply(payload: dict) -> dict:
    return {"provider": "email", "draft_id": "draft-placeholder", "payload": payload}


def send_email(payload: dict) -> dict:
    return {"provider": "email", "status": "sent", "payload": payload}
