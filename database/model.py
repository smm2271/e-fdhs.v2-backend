"""SQLAlchemy models generated from database/db.dbml."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    FetchedValue,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


UTC_NOW = text("timezone('utc', now())")


class AccountType(str, enum.Enum):
    STUDENT = "student"
    TEACHER = "teacher"


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
        Index(
            "uq_accounts_student_account_position_id",
            "account",
            "position_id",
            unique=True,
            postgresql_where=text("account_type = 'student'"),
        ),
        Index(
            "uq_accounts_teacher_account",
            "account",
            unique=True,
            postgresql_where=text("account_type = 'teacher'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(
            AccountType,
            name="account_type",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
    )
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
    sessions: Mapped[list["Session"]] = relationship(
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


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_sessions_token_hash"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    force_ttl_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("720")
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False))

    account: Mapped["Account"] = relationship(back_populates="sessions")


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )
    # The service layer supplies the deadline for the effective confirmation cycle.
    ack_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    author: Mapped["Account"] = relationship()
    targets: Mapped[list["BroadcastTarget"]] = relationship(
        back_populates="broadcast", passive_deletes=True
    )
    target_groups: Mapped[list["Group"]] = relationship(
        secondary="broadcast_targets", viewonly=True
    )
    confirmations: Mapped[list["BroadcastConfirmation"]] = relationship(
        back_populates="broadcast", passive_deletes=True
    )
    replies: Mapped[list["Reply"]] = relationship(
        primaryjoin="Broadcast.id == foreign(Reply.broadcast_id)", viewonly=True
    )


class BroadcastTarget(Base):
    __tablename__ = "broadcast_targets"

    broadcast_id: Mapped[UUID] = mapped_column(ForeignKey("broadcasts.id"), primary_key=True)
    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id"), primary_key=True)

    broadcast: Mapped["Broadcast"] = relationship(back_populates="targets")
    group: Mapped["Group"] = relationship()
    replies: Mapped[list["Reply"]] = relationship(
        back_populates="target", passive_deletes=True
    )


class BroadcastConfirmation(Base):
    __tablename__ = "broadcast_confirmations"

    broadcast_id: Mapped[UUID] = mapped_column(ForeignKey("broadcasts.id"), primary_key=True)
    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id"), primary_key=True)
    confirmed_by_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["broadcast_id", "group_id"],
            ["broadcast_targets.broadcast_id", "broadcast_targets.group_id"],
            name="fk_confirmations_target",
        ),
    )

    # Target membership is also enforced by the database.
    broadcast: Mapped["Broadcast"] = relationship(back_populates="confirmations")
    group: Mapped["Group"] = relationship()
    confirmed_by_account: Mapped["Account"] = relationship()


class Reply(Base):
    __tablename__ = "replies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["broadcast_id", "group_id"],
            ["broadcast_targets.broadcast_id", "broadcast_targets.group_id"],
            name="fk_replies_broadcast_group_target",
        ),
        UniqueConstraint("id", "broadcast_id", "group_id", name="uq_replies_scope"),
        ForeignKeyConstraint(
            ["ref_id", "broadcast_id", "group_id"],
            ["replies.id", "replies.broadcast_id", "replies.group_id"],
            name="fk_replies_parent_scope",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    broadcast_id: Mapped[UUID] = mapped_column(nullable=False)
    group_id: Mapped[UUID] = mapped_column(nullable=False)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("replies.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=UTC_NOW
    )

    target: Mapped["BroadcastTarget"] = relationship(back_populates="replies")
    broadcast: Mapped["Broadcast"] = relationship(
        primaryjoin="foreign(Reply.broadcast_id) == Broadcast.id", viewonly=True
    )
    author: Mapped["Account"] = relationship()
    # Both the service and composite FK enforce the referenced target thread.
    referenced_reply: Mapped[Optional["Reply"]] = relationship(
        primaryjoin="foreign(Reply.ref_id) == remote(Reply.id)",
        foreign_keys="Reply.ref_id", remote_side="Reply.id"
    )
