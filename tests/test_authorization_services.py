from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from database.database import AsyncSessionLocal, engine
from database.service import (
    AccountService,
    ConflictError,
    GroupService,
    NotFoundError,
    PermissionOverrideService,
    PositionService,
    RoleService,
    SessionService,
)


pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_TEST_CONFIGURED") != "1",
    reason="PostgreSQL integration tests require TEST_DB_HOST, TEST_DB_PORT, TEST_DB_NAME, TEST_DB_USER, and TEST_DB_PASSWORD",
)


@pytest.fixture(scope="session", autouse=True)
def migrated_schema() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")


@pytest_asyncio.fixture(autouse=True)
async def empty_database() -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE sessions, permission_overrides, account_roles, accounts, roles, "
                "positions, groups RESTART IDENTITY"
            )
        )
    yield


@pytest.mark.asyncio
async def test_initial_migration_creates_dbml_tables_and_keys() -> None:
    async with engine.connect() as connection:
        schema = await connection.run_sync(
            lambda sync_connection: {
                "tables": set(inspect(sync_connection).get_table_names()),
                "account_unique": inspect(sync_connection).get_unique_constraints("accounts"),
                "account_role_pk": inspect(sync_connection).get_pk_constraint("account_roles"),
                "account_foreign_keys": inspect(sync_connection).get_foreign_keys("accounts"),
                "session_unique": inspect(sync_connection).get_unique_constraints("sessions"),
                "session_foreign_keys": inspect(sync_connection).get_foreign_keys("sessions"),
            }
        )

    assert {
        "groups",
        "positions",
        "accounts",
        "roles",
        "account_roles",
        "permission_overrides",
        "sessions",
    }.issubset(schema["tables"])
    assert any(
        constraint["column_names"] == ["account", "position_id"]
        for constraint in schema["account_unique"]
    )
    assert schema["account_role_pk"]["constrained_columns"] == ["account_id", "role_id"]
    assert {foreign_key["referred_table"] for foreign_key in schema["account_foreign_keys"]} == {
        "groups",
        "positions",
    }
    assert any(
        constraint["column_names"] == ["token_hash"]
        for constraint in schema["session_unique"]
    )
    assert schema["session_foreign_keys"][0]["referred_table"] == "accounts"


@pytest.mark.asyncio
async def test_effective_permissions_honors_active_overrides_and_deny() -> None:
    async with AsyncSessionLocal() as session:
        groups = GroupService(session)
        positions = PositionService(session)
        roles = RoleService(session)
        accounts = AccountService(session)
        overrides = PermissionOverrideService(session)

        group = await groups.create(group_type="class", name="Class 101")
        position = await positions.create(name="student", permissions=0b0011)
        role = await roles.create(name="monitor", permissions=0b1100)
        account = await accounts.create(
            account="320001",
            position_id=position.id,
            password_hash="already-hashed",
            group_id=group.id,
        )
        await accounts.assign_role(account.id, role.id)
        now = datetime.now(timezone.utc)
        await overrides.create(
            account_id=account.id,
            allow_permissions=0b1_0000,
            deny_permissions=0b0100,
            starts_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=1),
        )
        await overrides.create(
            account_id=account.id,
            allow_permissions=0b10_0000,
            starts_at=now + timedelta(minutes=1),
        )

        assert await accounts.effective_permissions(account.id, now=now) == 0b1_1011


@pytest.mark.asyncio
async def test_group_cycles_and_referenced_deletes_are_rejected() -> None:
    async with AsyncSessionLocal() as session:
        groups = GroupService(session)
        positions = PositionService(session)
        accounts = AccountService(session)

        root = await groups.create(group_type="department", name="Root")
        child = await groups.create(
            group_type="class", name="Child", parent_id=root.id
        )
        with pytest.raises(ConflictError):
            await groups.update(root.id, parent_id=child.id)

        position = await positions.create(name="teacher")
        await accounts.create(
            account="teacher-1",
            position_id=position.id,
            password_hash="already-hashed",
            group_id=child.id,
        )
        with pytest.raises(ConflictError):
            await groups.delete(child.id)
        with pytest.raises(ConflictError):
            await positions.delete(position.id)


@pytest.mark.asyncio
async def test_unique_assignments_and_database_updated_at() -> None:
    async with AsyncSessionLocal() as session:
        groups = GroupService(session)
        positions = PositionService(session)
        roles = RoleService(session)
        accounts = AccountService(session)

        group = await groups.create(group_type="class", name="Class 102")
        position = await positions.create(name="assistant")
        role = await roles.create(name="reader")
        account = await accounts.create(
            account="320002",
            position_id=position.id,
            password_hash="already-hashed",
            group_id=group.id,
        )
        original_updated_at = account.updated_at
        await accounts.assign_role(account.id, role.id)
        with pytest.raises(ConflictError):
            await accounts.assign_role(account.id, role.id)

        await asyncio.sleep(0.001)
        await accounts.update(account.id, display_name="Updated")
        refreshed = await accounts.get(account.id)
        assert refreshed.created_at is not None
        assert refreshed.updated_at > original_updated_at


@pytest.mark.asyncio
async def test_sessions_only_resolve_while_unexpired_and_unrevoked() -> None:
    async with AsyncSessionLocal() as session:
        groups = GroupService(session)
        positions = PositionService(session)
        accounts = AccountService(session)
        sessions = SessionService(session)

        group = await groups.create(group_type="class", name="Class 103")
        position = await positions.create(name="session-test")
        assert (await positions.get_by_name("session-test")).id == position.id
        account = await accounts.create(
            account="320003",
            position_id=position.id,
            password_hash="already-hashed",
            group_id=group.id,
        )
        now = datetime.now(timezone.utc)
        active = await sessions.create(
            account_id=account.id,
            token_hash="hashed-active-token",
            expires_at=now + timedelta(minutes=5),
        )
        resolved = await sessions.get_active_by_token_hash(
            active.token_hash, now=now
        )
        assert resolved.id == active.id

        touched = await sessions.touch(active.id, used_at=now)
        assert touched.last_used_at == now.replace(tzinfo=None)
        await sessions.revoke(active.id, revoked_at=now)
        with pytest.raises(NotFoundError):
            await sessions.get_active_by_token_hash(active.token_hash, now=now)

        second_session = await sessions.create(
            account_id=account.id,
            token_hash="hashed-second-token",
            expires_at=now + timedelta(minutes=5),
        )
        assert await sessions.revoke_all_for_account(account.id, revoked_at=now) == 1
        with pytest.raises(NotFoundError):
            await sessions.get_active_by_token_hash(second_session.token_hash, now=now)
        with pytest.raises(ConflictError):
            await accounts.delete(account.id)
