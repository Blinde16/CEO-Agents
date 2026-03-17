from __future__ import annotations


INTEGRATION_TOKENS: dict[str, dict[str, dict]] = {}


def set_tokens(client_id: str, provider: str, token_payload: dict) -> None:
    INTEGRATION_TOKENS.setdefault(client_id, {})[provider] = token_payload


def get_tokens(client_id: str, provider: str) -> dict | None:
    return INTEGRATION_TOKENS.get(client_id, {}).get(provider)


def clear_tokens() -> None:
    INTEGRATION_TOKENS.clear()
