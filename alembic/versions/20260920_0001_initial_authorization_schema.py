"""Create the initial authorization schema.

Revision ID: 20260920_0001
Revises:
Create Date: 2026-09-20 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260920_0001"
down_revision = None
branch_labels = None
depends_on = None

UTC_NOW = sa.text("timezone('utc', now())")


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("propagate_confirmation", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(["parent_id"], ["groups.id"], name="fk_groups_parent_id_groups"),
    )
    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("permissions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.UniqueConstraint("name", name="uq_positions_name"),
    )
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("permissions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], name="fk_accounts_group_id_groups"),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"], name="fk_accounts_position_id_positions"),
        sa.UniqueConstraint("account", "position_id", name="uq_accounts_account_position_id"),
    )
    op.create_table(
        "account_roles",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name="fk_account_roles_account_id_accounts"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_account_roles_role_id_roles"),
        sa.PrimaryKeyConstraint("account_id", "role_id", name="pk_account_roles"),
    )
    op.create_table(
        "permission_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("allow_permissions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("deny_permissions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=UTC_NOW),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name="fk_permission_overrides_account_id_accounts"),
    )
    op.execute(
        """
        CREATE FUNCTION set_accounts_updated_at()
        RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = timezone('utc', now());
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER accounts_set_updated_at
        BEFORE UPDATE ON accounts
        FOR EACH ROW EXECUTE FUNCTION set_accounts_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER accounts_set_updated_at ON accounts")
    op.execute("DROP FUNCTION set_accounts_updated_at()")
    op.drop_table("permission_overrides")
    op.drop_table("account_roles")
    op.drop_table("accounts")
    op.drop_table("roles")
    op.drop_table("positions")
    op.drop_table("groups")
