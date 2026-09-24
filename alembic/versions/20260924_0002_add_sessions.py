"""Add persisted account sessions.

Revision ID: 20260924_0002
Revises: 20260920_0001
Create Date: 2026-09-24 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260924_0002"
down_revision = "20260920_0001"
branch_labels = None
depends_on = None

UTC_NOW = sa.text("timezone('utc', now())")


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.Column("last_used_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_sessions_account_id_accounts"
        ),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("sessions")
