"""SQLAlchemy models generated from database/db.dbml."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    FetchedValue,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


UTC_NOW = text("timezone('utc', now())")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    propagate_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    parent_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("groups.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )

    parent: Mapped[Optional["Group"]] = relationship(
        back_populates="children", remote_side="Group.id", passive_deletes=True
    )
    children: Mapped[list["Group"]] = relationship(
        back_populates="parent", passive_deletes=True
    )
    accounts: Mapped[list["Account"]] = relationship(
        back_populates="group", passive_deletes=True
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("name", name="uq_positions_name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    permissions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    accounts: Mapped[list["Account"]] = relationship(
        back_populates="position", passive_deletes=True
    )


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("account", "position_id", name="uq_accounts_account_position_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    position_id: Mapped[UUID] = mapped_column(ForeignKey("positions.id"), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id"), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=UTC_NOW,
        server_onupdate=FetchedValue(),
    )

    position: Mapped["Position"] = relationship(back_populates="accounts")
    group: Mapped["Group"] = relationship(back_populates="accounts")
    role_assignments: Mapped[list["AccountRole"]] = relationship(
        back_populates="account", passive_deletes=True
    )
    permission_overrides: Mapped[list["PermissionOverride"]] = relationship(
        back_populates="account", passive_deletes=True
    )
    roles: Mapped[list["Role"]] = relationship(
        secondary="account_roles", back_populates="accounts", viewonly=True
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("name", name="uq_roles_name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    permissions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    account_assignments: Mapped[list["AccountRole"]] = relationship(
        back_populates="role", passive_deletes=True
    )
    accounts: Mapped[list["Account"]] = relationship(
        secondary="account_roles", back_populates="roles", viewonly=True
    )


class AccountRole(Base):
    __tablename__ = "account_roles"

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )

    account: Mapped["Account"] = relationship(back_populates="role_assignments")
    role: Mapped["Role"] = relationship(back_populates="account_assignments")


class PermissionOverride(Base):
    __tablename__ = "permission_overrides"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    allow_permissions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    deny_permissions: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )

    account: Mapped["Account"] = relationship(back_populates="permission_overrides")
