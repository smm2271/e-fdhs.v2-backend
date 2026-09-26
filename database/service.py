"""Asynchronous CRUD and authorization services for the database models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .model import (
    Account,
    AccountRole,
    AccountType,
    Group,
    PermissionOverride,
    Position,
    Role,
    Session,
)


class ServiceError(Exception):
    """Base class for expected data-service failures."""


class NotFoundError(ServiceError):
    """Raised when the requested resource does not exist."""


class ConflictError(ServiceError):
    """Raised when an operation violates a business or database constraint."""


class ValidationError(ServiceError):
    """Raised when supplied data is structurally invalid."""


_UNSET: Final = object()


def _validate_page(limit: int, offset: int) -> None:
    if limit < 1 or limit > 500:
        raise ValidationError("limit must be between 1 and 500")
    if offset < 0:
        raise ValidationError("offset must be non-negative")


def _utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _validate_override_window(
    starts_at: Optional[datetime], expires_at: Optional[datetime]
) -> tuple[Optional[datetime], Optional[datetime]]:
    starts_at = _utc_naive(starts_at)
    expires_at = _utc_naive(expires_at)
    if starts_at is not None and expires_at is not None and expires_at <= starts_at:
        raise ValidationError("expires_at must be later than starts_at")
    return starts_at, expires_at


async def _commit_and_refresh(session: AsyncSession, entity: object) -> None:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError("Operation conflicts with existing database data") from error
    await session.refresh(entity)


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError("Operation conflicts with existing database data") from error


class GroupService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        group_type: str,
        name: str,
        parent_id: Optional[UUID] = None,
        propagate_confirmation: bool = True,
    ) -> Group:
        await self._validate_parent(None, parent_id)
        group = Group(
            id=uuid4(),
            type=group_type,
            name=name,
            parent_id=parent_id,
            propagate_confirmation=propagate_confirmation,
        )
        self.session.add(group)
        await _commit_and_refresh(self.session, group)
        return group

    async def get(self, group_id: UUID) -> Group:
        group = await self.session.get(Group, group_id)
        if group is None:
            raise NotFoundError(f"Group {group_id} was not found")
        return group

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[Group]:
        _validate_page(limit, offset)
        result = await self.session.scalars(
            select(Group).order_by(Group.name, Group.id).limit(limit).offset(offset)
        )
        return list(result)

    async def update(
        self,
        group_id: UUID,
        *,
        group_type: str | object = _UNSET,
        name: str | object = _UNSET,
        parent_id: Optional[UUID] | object = _UNSET,
        propagate_confirmation: bool | object = _UNSET,
    ) -> Group:
        group = await self.get(group_id)
        if parent_id is not _UNSET:
            await self._validate_parent(group.id, parent_id)
            group.parent_id = parent_id
        if group_type is not _UNSET:
            group.type = group_type
        if name is not _UNSET:
            group.name = name
        if propagate_confirmation is not _UNSET:
            group.propagate_confirmation = propagate_confirmation
        await _commit_and_refresh(self.session, group)
        return group

    async def delete(self, group_id: UUID) -> None:
        await self.get(group_id)
        child_id = await self.session.scalar(
            select(Group.id).where(Group.parent_id == group_id).limit(1)
        )
        account_id = await self.session.scalar(
            select(Account.id).where(Account.group_id == group_id).limit(1)
        )
        if child_id is not None or account_id is not None:
            raise ConflictError("Cannot delete a group that is referenced by a child or account")
        await self.session.delete(await self.get(group_id))
        await _commit(self.session)

    async def _validate_parent(
        self, group_id: Optional[UUID], parent_id: Optional[UUID]
    ) -> None:
        if parent_id is None:
            return
        if group_id is not None and group_id == parent_id:
            raise ConflictError("A group cannot be its own parent")

        parent = await self.session.get(Group, parent_id)
        if parent is None:
            raise NotFoundError(f"Parent group {parent_id} was not found")

        seen: set[UUID] = set()
        current: Optional[Group] = parent
        while current is not None:
            if current.id in seen:
                raise ConflictError("The existing group hierarchy contains a cycle")
            seen.add(current.id)
            if group_id is not None and current.id == group_id:
                raise ConflictError("A group cannot have one of its descendants as parent")
            current = (
                await self.session.get(Group, current.parent_id)
                if current.parent_id is not None
                else None
            )


class PositionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, name: str, permissions: int = 0, description: Optional[str] = None
    ) -> Position:
        position = Position(id=uuid4(), name=name, permissions=permissions, description=description)
        self.session.add(position)
        await _commit_and_refresh(self.session, position)
        return position

    async def get(self, position_id: UUID) -> Position:
        position = await self.session.get(Position, position_id)
        if position is None:
            raise NotFoundError(f"Position {position_id} was not found")
        return position

    async def get_by_name(self, name: str) -> Position:
        position = await self.session.scalar(
            select(Position).where(Position.name == name)
        )
        if position is None:
            raise NotFoundError(f"Position '{name}' was not found")
        return position

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[Position]:
        _validate_page(limit, offset)
        result = await self.session.scalars(
            select(Position).order_by(Position.name, Position.id).limit(limit).offset(offset)
        )
        return list(result)

    async def update(
        self,
        position_id: UUID,
        *,
        name: str | object = _UNSET,
        permissions: int | object = _UNSET,
        description: Optional[str] | object = _UNSET,
    ) -> Position:
        position = await self.get(position_id)
        if name is not _UNSET:
            position.name = name
        if permissions is not _UNSET:
            position.permissions = permissions
        if description is not _UNSET:
            position.description = description
        await _commit_and_refresh(self.session, position)
        return position

    async def delete(self, position_id: UUID) -> None:
        await self.get(position_id)
        account_id = await self.session.scalar(
            select(Account.id).where(Account.position_id == position_id).limit(1)
        )
        if account_id is not None:
            raise ConflictError("Cannot delete a position that is referenced by an account")
        await self.session.delete(await self.get(position_id))
        await _commit(self.session)


class RoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, name: str, permissions: int = 0, description: Optional[str] = None
    ) -> Role:
        role = Role(id=uuid4(), name=name, permissions=permissions, description=description)
        self.session.add(role)
        await _commit_and_refresh(self.session, role)
        return role

    async def get(self, role_id: UUID) -> Role:
        role = await self.session.get(Role, role_id)
        if role is None:
            raise NotFoundError(f"Role {role_id} was not found")
        return role

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[Role]:
        _validate_page(limit, offset)
        result = await self.session.scalars(
            select(Role).order_by(Role.name, Role.id).limit(limit).offset(offset)
        )
        return list(result)

    async def update(
        self,
        role_id: UUID,
        *,
        name: str | object = _UNSET,
        permissions: int | object = _UNSET,
        description: Optional[str] | object = _UNSET,
    ) -> Role:
        role = await self.get(role_id)
        if name is not _UNSET:
            role.name = name
        if permissions is not _UNSET:
            role.permissions = permissions
        if description is not _UNSET:
            role.description = description
        await _commit_and_refresh(self.session, role)
        return role

    async def delete(self, role_id: UUID) -> None:
        await self.get(role_id)
        assignment_id = await self.session.scalar(
            select(AccountRole.account_id).where(AccountRole.role_id == role_id).limit(1)
        )
        if assignment_id is not None:
            raise ConflictError("Cannot delete a role that is assigned to an account")
        await self.session.delete(await self.get(role_id))
        await _commit(self.session)


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        account: str,
        account_type: AccountType,
        position_id: UUID,
        password_hash: str,
        group_id: UUID,
        display_name: Optional[str] = None,
        is_active: bool = True,
    ) -> Account:
        await PositionService(self.session).get(position_id)
        await GroupService(self.session).get(group_id)
        model = Account(
            id=uuid4(),
            account=account,
            account_type=account_type,
            position_id=position_id,
            password_hash=password_hash,
            group_id=group_id,
            display_name=display_name,
            is_active=is_active,
        )
        self.session.add(model)
        await _commit_and_refresh(self.session, model)
        return model

    async def get(self, account_id: UUID) -> Account:
        account = await self.session.get(Account, account_id)
        if account is None:
            raise NotFoundError(f"Account {account_id} was not found")
        return account

    async def get_by_account_position(
        self, account: str, position_id: UUID
    ) -> Account:
        model = await self.session.scalar(
            select(Account).where(
                Account.account == account,
                Account.account_type == AccountType.STUDENT,
                Account.position_id == position_id,
            )
        )
        if model is None:
            raise NotFoundError(
                f"Account '{account}' with position {position_id} was not found"
            )
        return model

    async def get_teacher_by_account(self, account: str) -> Account:
        model = await self.session.scalar(
            select(Account).where(
                Account.account == account,
                Account.account_type == AccountType.TEACHER,
            )
        )
        if model is None:
            raise NotFoundError(f"Teacher account '{account}' was not found")
        return model

    async def list(
        self,
        *,
        group_id: Optional[UUID] = None,
        position_id: Optional[UUID] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Account]:
        _validate_page(limit, offset)
        query = select(Account).order_by(Account.account, Account.id)
        if group_id is not None:
            query = query.where(Account.group_id == group_id)
        if position_id is not None:
            query = query.where(Account.position_id == position_id)
        result = await self.session.scalars(query.limit(limit).offset(offset))
        return list(result)

    async def update(
        self,
        account_id: UUID,
        *,
        account: str | object = _UNSET,
        account_type: AccountType | object = _UNSET,
        position_id: UUID | object = _UNSET,
        password_hash: str | object = _UNSET,
        group_id: UUID | object = _UNSET,
        display_name: Optional[str] | object = _UNSET,
        is_active: bool | object = _UNSET,
    ) -> Account:
        model = await self.get(account_id)
        if position_id is not _UNSET:
            await PositionService(self.session).get(position_id)
            model.position_id = position_id
        if group_id is not _UNSET:
            await GroupService(self.session).get(group_id)
            model.group_id = group_id
        if account is not _UNSET:
            model.account = account
        if account_type is not _UNSET:
            model.account_type = account_type
        if password_hash is not _UNSET:
            model.password_hash = password_hash
        if display_name is not _UNSET:
            model.display_name = display_name
        if is_active is not _UNSET:
            model.is_active = is_active
        await _commit_and_refresh(self.session, model)
        return model

    async def update_password_without_commit(
        self, account_id: UUID, *, password_hash: str
    ) -> Account:
        """Stage a password change for a caller-managed transaction."""
        model = await self.get(account_id)
        model.password_hash = password_hash
        await self.session.flush()
        return model

    async def delete(self, account_id: UUID) -> None:
        await self.get(account_id)
        role_id = await self.session.scalar(
            select(AccountRole.role_id).where(AccountRole.account_id == account_id).limit(1)
        )
        override_id = await self.session.scalar(
            select(PermissionOverride.id)
            .where(PermissionOverride.account_id == account_id)
            .limit(1)
        )
        session_id = await self.session.scalar(
            select(Session.id).where(Session.account_id == account_id).limit(1)
        )
        if role_id is not None or override_id is not None or session_id is not None:
            raise ConflictError(
                "Cannot delete an account with roles, permission overrides, or sessions"
            )
        await self.session.delete(await self.get(account_id))
        await _commit(self.session)

    async def assign_role(self, account_id: UUID, role_id: UUID) -> AccountRole:
        await self.get(account_id)
        await RoleService(self.session).get(role_id)
        existing = await self.session.get(AccountRole, (account_id, role_id))
        if existing is not None:
            raise ConflictError("The role is already assigned to this account")
        assignment = AccountRole(account_id=account_id, role_id=role_id)
        self.session.add(assignment)
        await _commit_and_refresh(self.session, assignment)
        return assignment

    async def remove_role(self, account_id: UUID, role_id: UUID) -> None:
        assignment = await self.session.get(AccountRole, (account_id, role_id))
        if assignment is None:
            raise NotFoundError("The role is not assigned to this account")
        await self.session.delete(assignment)
        await _commit(self.session)

    async def effective_permissions(
        self, account_id: UUID, *, now: Optional[datetime] = None
    ) -> int:
        model = await self.session.scalar(
            select(Account)
            .where(Account.id == account_id)
            .options(
                selectinload(Account.position),
                selectinload(Account.role_assignments).selectinload(AccountRole.role),
                selectinload(Account.permission_overrides),
            )
        )
        if model is None:
            raise NotFoundError(f"Account {account_id} was not found")

        current_time = _utc_naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
        permissions = model.position.permissions
        for assignment in model.role_assignments:
            permissions |= assignment.role.permissions

        allowed = 0
        denied = 0
        for override in model.permission_overrides:
            if _is_override_active(override, current_time):
                allowed |= override.allow_permissions
                denied |= override.deny_permissions
        return (permissions | allowed) & ~denied


class SessionService:
    """Persist and manage hashed account-session tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        account_id: UUID,
        token_hash: str,
        expires_at: datetime,
        force_ttl_hours: int,
    ) -> Session:
        await AccountService(self.session).get(account_id)
        expires_at = _utc_naive(expires_at)
        if force_ttl_hours <= 0:
            raise ValidationError("force_ttl_hours must be positive")
        model = Session(
            id=uuid4(),
            account_id=account_id,
            token_hash=token_hash,
            expires_at=expires_at,
            force_ttl_hours=force_ttl_hours,
        )
        self.session.add(model)
        await _commit_and_refresh(self.session, model)
        return model

    async def get(self, session_id: UUID) -> Session:
        model = await self.session.get(Session, session_id)
        if model is None:
            raise NotFoundError(f"Session {session_id} was not found")
        return model

    async def get_active_by_token_hash(
        self, token_hash: str, *, now: Optional[datetime] = None
    ) -> Session:
        current_time = _utc_naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
        model = await self.session.scalar(
            select(Session).where(
                Session.token_hash == token_hash,
                Session.revoked_at.is_(None),
                Session.expires_at > current_time,
            )
        )
        if model is None or _session_force_expires_at(model) <= current_time:
            raise NotFoundError("Active session token was not found")
        return model

    async def list_for_account(
        self, account_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[Session]:
        _validate_page(limit, offset)
        result = await self.session.scalars(
            select(Session)
            .where(Session.account_id == account_id)
            .order_by(Session.created_at.desc(), Session.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result)

    async def update(
        self,
        session_id: UUID,
        *,
        expires_at: datetime | object = _UNSET,
        last_used_at: datetime | object = _UNSET,
        force_ttl_hours: int | object = _UNSET,
        revoked_at: Optional[datetime] | object = _UNSET,
    ) -> Session:
        model = await self.get(session_id)
        if expires_at is not _UNSET:
            model.expires_at = _utc_naive(expires_at)
        if last_used_at is not _UNSET:
            model.last_used_at = _utc_naive(last_used_at)
        if force_ttl_hours is not _UNSET:
            if force_ttl_hours <= 0:
                raise ValidationError("force_ttl_hours must be positive")
            model.force_ttl_hours = force_ttl_hours
        if revoked_at is not _UNSET:
            model.revoked_at = _utc_naive(revoked_at)
        await _commit_and_refresh(self.session, model)
        return model

    async def touch(
        self,
        session_id: UUID,
        *,
        renewal_ttl: timedelta,
        renewal_window: timedelta,
        used_at: Optional[datetime] = None,
    ) -> Session:
        if renewal_ttl <= timedelta(0):
            raise ValidationError("renewal_ttl must be positive")
        if renewal_window < timedelta(0):
            raise ValidationError("renewal_window must be non-negative")

        current_time = _utc_naive(used_at) or datetime.now(timezone.utc).replace(
            tzinfo=None
        )
        model = await self.get(session_id)
        force_expires_at = _session_force_expires_at(model)
        if (
            model.revoked_at is not None
            or model.expires_at <= current_time
            or force_expires_at <= current_time
        ):
            raise NotFoundError("Active session was not found")

        renew = (
            model.expires_at - current_time <= renewal_window
            and model.expires_at < force_expires_at
        )
        # Do not turn every authenticated request into a database write.  The
        # timestamp is retained for auditing, but is sampled at five-minute intervals.
        update_last_used = (
            used_at is not None
            or model.last_used_at <= current_time - timedelta(minutes=5)
        )
        if renew:
            model.expires_at = min(
                current_time + renewal_ttl, force_expires_at
            )
        if update_last_used:
            model.last_used_at = current_time
        if renew or update_last_used:
            await self.session.flush()
            await self.session.commit()
        return model

    async def revoke(
        self, session_id: UUID, *, revoked_at: Optional[datetime] = None
    ) -> Session:
        return await self.update(
            session_id,
            revoked_at=_utc_naive(revoked_at)
            or datetime.now(timezone.utc).replace(tzinfo=None),
        )

    async def revoke_all_for_account(
        self, account_id: UUID, *, revoked_at: Optional[datetime] = None
    ) -> int:
        count = await self.revoke_all_for_account_without_commit(
            account_id, revoked_at=revoked_at
        )
        await _commit(self.session)
        return count

    async def revoke_all_for_account_without_commit(
        self, account_id: UUID, *, revoked_at: Optional[datetime] = None
    ) -> int:
        """Stage revocation for a caller-managed transaction."""
        await AccountService(self.session).get(account_id)
        revoked_at = _utc_naive(revoked_at) or datetime.now(timezone.utc).replace(tzinfo=None)
        result = await self.session.execute(
            update(Session)
            .where(Session.account_id == account_id, Session.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount or 0

class PermissionOverrideService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        account_id: UUID,
        allow_permissions: int = 0,
        deny_permissions: int = 0,
        reason: Optional[str] = None,
        starts_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
    ) -> PermissionOverride:
        await AccountService(self.session).get(account_id)
        starts_at, expires_at = _validate_override_window(starts_at, expires_at)
        override = PermissionOverride(
            id=uuid4(),
            account_id=account_id,
            allow_permissions=allow_permissions,
            deny_permissions=deny_permissions,
            reason=reason,
            starts_at=starts_at,
            expires_at=expires_at,
        )
        self.session.add(override)
        await _commit_and_refresh(self.session, override)
        return override

    async def get(self, override_id: UUID) -> PermissionOverride:
        override = await self.session.get(PermissionOverride, override_id)
        if override is None:
            raise NotFoundError(f"Permission override {override_id} was not found")
        return override

    async def list_for_account(
        self, account_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[PermissionOverride]:
        _validate_page(limit, offset)
        result = await self.session.scalars(
            select(PermissionOverride)
            .where(PermissionOverride.account_id == account_id)
            .order_by(PermissionOverride.created_at, PermissionOverride.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result)

    async def update(
        self,
        override_id: UUID,
        *,
        allow_permissions: int | object = _UNSET,
        deny_permissions: int | object = _UNSET,
        reason: Optional[str] | object = _UNSET,
        starts_at: Optional[datetime] | object = _UNSET,
        expires_at: Optional[datetime] | object = _UNSET,
    ) -> PermissionOverride:
        override = await self.get(override_id)
        new_starts = override.starts_at if starts_at is _UNSET else starts_at
        new_expires = override.expires_at if expires_at is _UNSET else expires_at
        new_starts, new_expires = _validate_override_window(new_starts, new_expires)
        if allow_permissions is not _UNSET:
            override.allow_permissions = allow_permissions
        if deny_permissions is not _UNSET:
            override.deny_permissions = deny_permissions
        if reason is not _UNSET:
            override.reason = reason
        if starts_at is not _UNSET:
            override.starts_at = new_starts
        if expires_at is not _UNSET:
            override.expires_at = new_expires
        await _commit_and_refresh(self.session, override)
        return override

    async def delete(self, override_id: UUID) -> None:
        override = await self.get(override_id)
        await self.session.delete(override)
        await _commit(self.session)


def _is_override_active(override: PermissionOverride, now: datetime) -> bool:
    return (
        (override.starts_at is None or override.starts_at <= now)
        and (override.expires_at is None or override.expires_at > now)
    )


def _session_force_expires_at(session: Session) -> datetime:
    return session.created_at + timedelta(hours=session.force_ttl_hours)
