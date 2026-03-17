from __future__ import annotations

import os
from pathlib import Path


TEST_DB_PATH = Path(__file__).resolve().parent / "test_ceo_agents.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ.setdefault("N8N_WEBHOOK_SECRET", "test-secret")
