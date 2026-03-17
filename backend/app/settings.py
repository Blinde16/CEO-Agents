from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    # Fast/cheap model for reads, triage, simple ops
    claude_model: str
    # Smart model for drafts, briefings, complex reasoning
    claude_model_heavy: str
    google_client_id: str | None
    google_client_secret: str | None
    google_redirect_uri: str
    app_base_url: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        claude_model_heavy=os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-6"),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID"),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/integrations/google/callback"),
        app_base_url=os.getenv("APP_BASE_URL", "http://localhost:3000"),
    )
