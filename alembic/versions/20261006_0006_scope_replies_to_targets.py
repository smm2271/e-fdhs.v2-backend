"""Scope replies to broadcast targets without guessing historical thread ownership.

Revision ID: 20261006_0006
Revises: 20261005_0005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261006_0006"
down_revision = "20261005_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the preflight and backfill consistent with concurrent legacy writes.
    op.execute("LOCK TABLE replies, broadcast_targets IN ACCESS EXCLUSIVE MODE")
    # A legacy reply can be mapped safely only if its broadcast has one target.
    # Fail before DDL when ownership is ambiguous; do not infer it from the author.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM replies r
                WHERE (SELECT count(*) FROM broadcast_targets t
                       WHERE t.broadcast_id = r.broadcast_id) <> 1
            ) THEN
                RAISE EXCEPTION 'Cannot scope legacy replies: each replied-to broadcast must have exactly one target. Resolve historical thread ownership manually before upgrading.';
            END IF;
        END $$
    """)
    op.add_column("replies", sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute("""
        UPDATE replies r SET group_id = t.group_id
        FROM broadcast_targets t WHERE t.broadcast_id = r.broadcast_id
    """)
    op.alter_column("replies", "group_id", nullable=False)
    op.drop_constraint("fk_replies_broadcast_id_broadcasts", "replies", type_="foreignkey")
    op.create_foreign_key(
        "fk_replies_broadcast_group_target", "replies", "broadcast_targets",
        ["broadcast_id", "group_id"], ["broadcast_id", "group_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_replies_broadcast_group_target", "replies", type_="foreignkey")
    op.create_foreign_key(
        "fk_replies_broadcast_id_broadcasts", "replies", "broadcasts",
        ["broadcast_id"], ["id"],
    )
    op.drop_column("replies", "group_id")
