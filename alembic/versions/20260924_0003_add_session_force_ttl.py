"""Add per-session non-renewable expiration duration.

Revision ID: 20260924_0003
Revises: 20260924_0002
Create Date: 2026-09-24 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260924_0003"
down_revision = "20260924_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "force_ttl_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("720"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "force_ttl_hours")
