from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ClientModel(Base):
    __tablename__ = "clients"

    client_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    working_hours: Mapped[str] = mapped_column(String, nullable=False)
    scheduling_preferences: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    approval_rules: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    priority_contacts: Mapped[list] = mapped_column(JSON, default=lambda: [])
    voice_examples: Mapped[list] = mapped_column(JSON, default=lambda: [])
    # Stored as list of {action_type, rule, created_at} dicts
    learned_preferences: Mapped[list] = mapped_column(JSON, default=lambda: [])
    focus_blocks: Mapped[list] = mapped_column(JSON, default=lambda: [])


class ActionModel(Base):
    __tablename__ = "actions"

    action_id: Mapped[str] = mapped_column(String, primary_key=True)
    client_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    status: Mapped[str] = mapped_column(String, nullable=False)
    approval_status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalModel(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String, primary_key=True)
    action_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionLogModel(Base):
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    action_status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_by: Mapped[str] = mapped_column(String, nullable=False)
    approval_status: Mapped[str] = mapped_column(String, nullable=False)


class IntegrationModel(Base):
    """One row per (client_id, provider). Stores both metadata and OAuth tokens."""

    __tablename__ = "integrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="connected")
    connected_account: Mapped[str | None] = mapped_column(String, nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: [])
    # Full OAuth token payload (access_token, refresh_token, expires_at, etc.)
    tokens: Mapped[dict] = mapped_column(JSON, default=lambda: {})


class OAuthStateModel(Base):
    """Short-lived nonce records for OAuth CSRF protection."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String, primary_key=True)
    client_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
