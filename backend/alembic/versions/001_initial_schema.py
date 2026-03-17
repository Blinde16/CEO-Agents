"""Initial schema — clients, actions, approvals, logs, integrations, oauth_states

Revision ID: 001
Revises:
Create Date: 2026-03-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("client_id", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("working_hours", sa.String(), nullable=False),
        sa.Column("scheduling_preferences", sa.JSON(), nullable=True),
        sa.Column("approval_rules", sa.JSON(), nullable=True),
        sa.Column("priority_contacts", sa.JSON(), nullable=True),
        sa.Column("voice_examples", sa.JSON(), nullable=True),
        sa.Column("learned_preferences", sa.JSON(), nullable=True),
        sa.Column("focus_blocks", sa.JSON(), nullable=True),
    )

    op.create_table(
        "actions",
        sa.Column("action_id", sa.String(), primary_key=True),
        sa.Column("client_id", sa.String(), nullable=False, index=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("approval_status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("reviewer_id", sa.String(), nullable=True),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(), primary_key=True),
        sa.Column("action_id", sa.String(), nullable=False, index=True),
        sa.Column("client_id", sa.String(), nullable=False, index=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reviewer_id", sa.String(), nullable=True),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "action_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("action_id", sa.String(), nullable=False, index=True),
        sa.Column("client_id", sa.String(), nullable=False, index=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("action_status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("executed_by", sa.String(), nullable=False),
        sa.Column("approval_status", sa.String(), nullable=False),
    )

    op.create_table(
        "integrations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.String(), nullable=False, index=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("connected_account", sa.String(), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("tokens", sa.JSON(), nullable=True),
    )

    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(), primary_key=True),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
    op.drop_table("integrations")
    op.drop_table("action_logs")
    op.drop_table("approvals")
    op.drop_table("actions")
    op.drop_table("clients")
