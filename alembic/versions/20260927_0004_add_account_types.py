"""Add account types and type-specific login identities.

Revision ID: 20260927_0004
Revises: 20260924_0003
Create Date: 2026-09-27 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260927_0004"
down_revision = "20260924_0003"
branch_labels = None
depends_on = None

account_type = postgresql.ENUM("student", "teacher", name="account_type")


def upgrade() -> None:
    account_type.create(op.get_bind(), checkfirst=False)
    op.add_column(
        "accounts",
        sa.Column("account_type", account_type, nullable=True),
    )
    op.execute("UPDATE accounts SET account_type = 'student'")
    op.alter_column("accounts", "account_type", nullable=False)

    op.drop_constraint(
        "uq_accounts_account_position_id", "accounts", type_="unique"
    )
    op.create_index(
        "uq_accounts_student_account_position_id",
        "accounts",
        ["account", "position_id"],
        unique=True,
        postgresql_where=sa.text("account_type = 'student'"),
    )
    op.create_index(
        "uq_accounts_teacher_account",
        "accounts",
        ["account"],
        unique=True,
        postgresql_where=sa.text("account_type = 'teacher'"),
    )


def downgrade() -> None:
    op.drop_index("uq_accounts_teacher_account", table_name="accounts")
    op.drop_index(
        "uq_accounts_student_account_position_id", table_name="accounts"
    )
    op.create_unique_constraint(
        "uq_accounts_account_position_id",
        "accounts",
        ["account", "position_id"],
    )
    op.drop_column("accounts", "account_type")
    account_type.drop(op.get_bind(), checkfirst=False)
