"""
Token store — thin delegation layer over the Database singleton.

External callers (google.py, etc.) import `get_tokens` / `set_tokens` from
here; all persistence now lives in the integrations table via database.py.
"""

from __future__ import annotations

from app.database import db


def set_tokens(client_id: str, provider: str, token_payload: dict) -> None:
    db.set_tokens(client_id, provider, token_payload)


def get_tokens(client_id: str, provider: str) -> dict | None:
    return db.get_tokens(client_id, provider)


def clear_tokens() -> None:
    # No-op: token cleanup is handled by db.clear_all() in the reset endpoint.
    pass
