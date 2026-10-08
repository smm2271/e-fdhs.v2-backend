"""Enforce parent thread and confirmation target integrity.

Revision ID: 20261008_0007
Revises: 20261006_0006
"""
from alembic import op

revision = "20261008_0007"
down_revision = "20261006_0006"
branch_labels = None
depends_on = None


def upgrade():
    # Existing invalid records fail validation; never guess or rewrite ownership.
    op.create_unique_constraint("uq_replies_scope", "replies", ["id", "broadcast_id", "group_id"])
    op.create_foreign_key("fk_replies_parent_scope", "replies", "replies",
                          ["ref_id", "broadcast_id", "group_id"], ["id", "broadcast_id", "group_id"])
    op.create_foreign_key("fk_confirmations_target", "broadcast_confirmations", "broadcast_targets",
                          ["broadcast_id", "group_id"], ["broadcast_id", "group_id"])


def downgrade():
    op.drop_constraint("fk_confirmations_target", "broadcast_confirmations", type_="foreignkey")
    op.drop_constraint("fk_replies_parent_scope", "replies", type_="foreignkey")
    op.drop_constraint("uq_replies_scope", "replies", type_="unique")
