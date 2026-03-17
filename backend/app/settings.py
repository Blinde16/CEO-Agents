from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    # Fast/cheap model for reads, triage, simple ops (gpt-4.1-mini)
    openai_model: str
    # Smart model for drafts, briefings, complex reasoning (gpt-4.1)
    openai_model_heavy: str
    google_client_id: str | None
    google_client_secret: str | None
    google_redirect_uri: str
    app_base_url: str
    # Postgres (or SQLite for local dev) — e.g. postgresql://user:pw@host/db
    database_url: str
    # Shared secret header sent by n8n webhooks for request verification
    n8n_webhook_secret: str | None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        openai_model_heavy=os.getenv("OPENAI_MODEL_HEAVY", "gpt-4.1"),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID"),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        google_redirect_uri=os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://localhost:8000/integrations/google/callback",
        ),
        app_base_url=os.getenv("APP_BASE_URL", "http://localhost:3000"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./ceo_agents.db"),
        n8n_webhook_secret=os.getenv("N8N_WEBHOOK_SECRET"),
    )
