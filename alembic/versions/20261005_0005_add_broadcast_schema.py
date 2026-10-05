"""Add broadcasts, targets, group confirmations, and replies.

Revision ID: 20261005_0005
Revises: 20260927_0004
Create Date: 2026-10-05 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261005_0005"
down_revision = "20260927_0004"
branch_labels = None
depends_on = None

UTC_NOW = sa.text("timezone('utc', now())")


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.Column("ack_deadline_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"], ["accounts.id"], name="fk_broadcasts_author_id_accounts"
        ),
    )
    op.create_table(
        "broadcast_targets",
        sa.Column("broadcast_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["broadcast_id"], ["broadcasts.id"], name="fk_broadcast_targets_broadcast_id_broadcasts"
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["groups.id"], name="fk_broadcast_targets_group_id_groups"
        ),
        sa.PrimaryKeyConstraint("broadcast_id", "group_id", name="pk_broadcast_targets"),
    )
    op.create_table(
        "broadcast_confirmations",
        sa.Column("broadcast_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_by_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(
            ["broadcast_id"], ["broadcasts.id"], name="fk_broadcast_confirmations_broadcast_id_broadcasts"
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["groups.id"], name="fk_broadcast_confirmations_group_id_groups"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_account_id"], ["accounts.id"],
            name="fk_broadcast_confirmations_confirmed_by_account_id_accounts",
        ),
        sa.PrimaryKeyConstraint("broadcast_id", "group_id", name="pk_broadcast_confirmations"),
    )
    op.create_table(
        "replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("broadcast_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(
            ["broadcast_id"], ["broadcasts.id"], name="fk_replies_broadcast_id_broadcasts"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["accounts.id"], name="fk_replies_author_id_accounts"
        ),
        sa.ForeignKeyConstraint(["ref_id"], ["replies.id"], name="fk_replies_ref_id_replies"),
    )


def downgrade() -> None:
    op.drop_table("replies")
    op.drop_table("broadcast_confirmations")
    op.drop_table("broadcast_targets")
    op.drop_table("broadcasts")
