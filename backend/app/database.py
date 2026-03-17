"""
Persistent database layer.

Replaces the in-memory dicts in main.py with a single SQLAlchemy-backed
Database class.  SQLite is the default (zero-config for local dev); switch to
Postgres by setting DATABASE_URL in the environment.

Usage (module-level singleton):
    from app.database import db
    client = db.get_client("acme-ceo")
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import create_engine, select, delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import (
    ActionLogModel,
    ActionModel,
    ApprovalModel,
    Base,
    ClientModel,
    IntegrationModel,
    OAuthStateModel,
)
from app.schemas import (
    ActionLog,
    ActionRecord,
    ActionStatus,
    ApprovalRecord,
    ApprovalStatus,
    ClientConfig,
    IntegrationRecord,
    LearnedPreference,
)


# ---------------------------------------------------------------------------
# Pydantic ↔ ORM conversion helpers
# ---------------------------------------------------------------------------

def _row_to_client(row: ClientModel) -> ClientConfig:
    return ClientConfig(
        client_id=row.client_id,
        display_name=row.display_name,
        timezone=row.timezone,
        working_hours=row.working_hours,
        scheduling_preferences=row.scheduling_preferences or {},
        approval_rules=row.approval_rules or {},
        priority_contacts=list(row.priority_contacts or []),
        voice_examples=list(row.voice_examples or []),
        learned_preferences=[
            LearnedPreference(**p) for p in (row.learned_preferences or [])
        ],
        focus_blocks=list(row.focus_blocks or []),
    )


def _row_to_action(row: ActionModel) -> ActionRecord:
    return ActionRecord(
        action_id=row.action_id,
        client_id=row.client_id,
        user_id=row.user_id,
        action_type=row.action_type,
        payload=row.payload or {},
        status=ActionStatus(row.status),
        approval_status=ApprovalStatus(row.approval_status),
        created_at=row.created_at,
        result=row.result,
        reviewer_id=row.reviewer_id,
        decision_time=row.decision_time,
    )


def _row_to_approval(row: ApprovalModel) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row.approval_id,
        action_id=row.action_id,
        client_id=row.client_id,
        status=ApprovalStatus(row.status),
        reviewer_id=row.reviewer_id,
        decision_time=row.decision_time,
    )


def _row_to_log(row: ActionLogModel) -> ActionLog:
    return ActionLog(
        action_id=row.action_id,
        client_id=row.client_id,
        user_id=row.user_id,
        timestamp=row.timestamp,
        action_type=row.action_type,
        action_status=ActionStatus(row.action_status),
        error_message=row.error_message,
        executed_by=row.executed_by,
        approval_status=ApprovalStatus(row.approval_status),
    )


def _row_to_integration(row: IntegrationModel) -> IntegrationRecord:
    return IntegrationRecord(
        client_id=row.client_id,
        provider=row.provider,
        status=row.status,
        connected_account=row.connected_account,
        scopes=list(row.scopes or []),
    )


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class Database:
    """
    Thin persistence façade.  All public methods open/commit/close their own
    session so callers don't need to manage transactions.
    """

    def __init__(self) -> None:
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = self._build_engine()
            Base.metadata.create_all(self._engine)
        return self._engine

    def _build_engine(self) -> Engine:
        # Import here to avoid module-level circular imports
        from app.settings import get_settings
        url = get_settings().database_url
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        return create_engine(url, **kwargs)

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        with Session(self.engine) as session:
            yield session

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    def get_client(self, client_id: str) -> ClientConfig | None:
        with self._session() as s:
            row = s.get(ClientModel, client_id)
            return _row_to_client(row) if row else None

    def upsert_client(self, config: ClientConfig) -> ClientConfig:
        with self._session() as s:
            row = s.get(ClientModel, config.client_id)
            if row is None:
                row = ClientModel(client_id=config.client_id)
                s.add(row)
            row.display_name = config.display_name
            row.timezone = config.timezone
            row.working_hours = config.working_hours
            row.scheduling_preferences = config.scheduling_preferences
            row.approval_rules = config.approval_rules
            row.priority_contacts = config.priority_contacts
            row.voice_examples = config.voice_examples
            row.learned_preferences = [p.model_dump() for p in config.learned_preferences]
            row.focus_blocks = config.focus_blocks
            s.commit()
            s.refresh(row)
            return _row_to_client(row)

    def list_clients(self) -> list[ClientConfig]:
        with self._session() as s:
            rows = s.scalars(select(ClientModel)).all()
            return [_row_to_client(r) for r in rows]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_action(self, action_id: str) -> ActionRecord | None:
        with self._session() as s:
            row = s.get(ActionModel, action_id)
            return _row_to_action(row) if row else None

    def save_action(self, action: ActionRecord) -> None:
        with self._session() as s:
            row = s.get(ActionModel, action.action_id)
            if row is None:
                row = ActionModel(action_id=action.action_id)
                s.add(row)
            row.client_id = action.client_id
            row.user_id = action.user_id
            row.action_type = action.action_type
            row.payload = action.payload
            row.status = action.status.value
            row.approval_status = action.approval_status.value
            row.created_at = action.created_at
            row.result = action.result
            row.reviewer_id = action.reviewer_id
            row.decision_time = action.decision_time
            s.commit()

    def list_actions(self, client_id: str | None = None) -> list[ActionRecord]:
        with self._session() as s:
            stmt = select(ActionModel).order_by(ActionModel.created_at.desc())
            if client_id:
                stmt = stmt.where(ActionModel.client_id == client_id)
            rows = s.scalars(stmt).all()
            return [_row_to_action(r) for r in rows]

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._session() as s:
            row = s.get(ApprovalModel, approval_id)
            return _row_to_approval(row) if row else None

    def save_approval(self, approval: ApprovalRecord) -> None:
        with self._session() as s:
            row = s.get(ApprovalModel, approval.approval_id)
            if row is None:
                row = ApprovalModel(approval_id=approval.approval_id)
                s.add(row)
            row.action_id = approval.action_id
            row.client_id = approval.client_id
            row.status = approval.status.value
            row.reviewer_id = approval.reviewer_id
            row.decision_time = approval.decision_time
            s.commit()

    def list_approvals(self, client_id: str | None = None) -> list[ApprovalRecord]:
        with self._session() as s:
            stmt = select(ApprovalModel).order_by(ApprovalModel.decision_time.desc())
            if client_id:
                stmt = stmt.where(ApprovalModel.client_id == client_id)
            rows = s.scalars(stmt).all()
            return [_row_to_approval(r) for r in rows]

    # ------------------------------------------------------------------
    # Action logs
    # ------------------------------------------------------------------

    def append_log(self, log: ActionLog) -> None:
        with self._session() as s:
            row = ActionLogModel(
                action_id=log.action_id,
                client_id=log.client_id,
                user_id=log.user_id,
                timestamp=log.timestamp,
                action_type=log.action_type,
                action_status=log.action_status.value,
                error_message=log.error_message,
                executed_by=log.executed_by,
                approval_status=log.approval_status.value,
            )
            s.add(row)
            s.commit()

    def list_logs(self, client_id: str | None = None) -> list[ActionLog]:
        with self._session() as s:
            stmt = select(ActionLogModel).order_by(ActionLogModel.timestamp.desc())
            if client_id:
                stmt = stmt.where(ActionLogModel.client_id == client_id)
            rows = s.scalars(stmt).all()
            return [_row_to_log(r) for r in rows]

    # ------------------------------------------------------------------
    # Integrations
    # ------------------------------------------------------------------

    def _get_integration_row(
        self, session: Session, client_id: str, provider: str
    ) -> IntegrationModel | None:
        stmt = select(IntegrationModel).where(
            IntegrationModel.client_id == client_id,
            IntegrationModel.provider == provider,
        )
        return session.scalars(stmt).first()

    def get_integration(
        self, client_id: str, provider: str
    ) -> IntegrationRecord | None:
        with self._session() as s:
            row = self._get_integration_row(s, client_id, provider)
            return _row_to_integration(row) if row else None

    def get_integrations(self, client_id: str) -> dict[str, IntegrationRecord]:
        with self._session() as s:
            stmt = select(IntegrationModel).where(
                IntegrationModel.client_id == client_id
            )
            rows = s.scalars(stmt).all()
            return {r.provider: _row_to_integration(r) for r in rows}

    def save_integration(
        self, record: IntegrationRecord, tokens: dict | None = None
    ) -> None:
        with self._session() as s:
            row = self._get_integration_row(s, record.client_id, record.provider)
            if row is None:
                row = IntegrationModel(
                    client_id=record.client_id, provider=record.provider
                )
                s.add(row)
            row.status = record.status
            row.connected_account = record.connected_account
            row.scopes = record.scopes
            if tokens is not None:
                row.tokens = tokens
            s.commit()

    # ------------------------------------------------------------------
    # OAuth states (short-lived CSRF nonces)
    # ------------------------------------------------------------------

    def put_oauth_state(self, state: str, client_id: str) -> None:
        with self._session() as s:
            s.add(
                OAuthStateModel(
                    state=state,
                    client_id=client_id,
                    created_at=datetime.now(timezone.utc),
                )
            )
            s.commit()

    def has_oauth_state(self, state: str) -> bool:
        with self._session() as s:
            return s.get(OAuthStateModel, state) is not None

    def pop_oauth_state(self, state: str) -> str | None:
        with self._session() as s:
            row = s.get(OAuthStateModel, state)
            if row is None:
                return None
            client_id = row.client_id
            s.delete(row)
            s.commit()
            return client_id

    # ------------------------------------------------------------------
    # Token store (used by integrations/store.py)
    # ------------------------------------------------------------------

    def get_tokens(self, client_id: str, provider: str) -> dict | None:
        with self._session() as s:
            row = self._get_integration_row(s, client_id, provider)
            return dict(row.tokens) if row and row.tokens else None

    def set_tokens(self, client_id: str, provider: str, tokens: dict) -> None:
        """Update (or create) the token payload for an integration row."""
        with self._session() as s:
            row = self._get_integration_row(s, client_id, provider)
            if row is None:
                row = IntegrationModel(
                    client_id=client_id,
                    provider=provider,
                    status="token_only",
                    tokens=tokens,
                )
                s.add(row)
            else:
                row.tokens = tokens
            s.commit()

    # ------------------------------------------------------------------
    # Demo reset
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Wipe all tables — demo/test only."""
        with self._session() as s:
            s.execute(delete(ActionLogModel))
            s.execute(delete(ApprovalModel))
            s.execute(delete(ActionModel))
            s.execute(delete(IntegrationModel))
            s.execute(delete(OAuthStateModel))
            s.execute(delete(ClientModel))
            s.commit()


# Module-level singleton — lazily initialises the engine on first use.
db = Database()
