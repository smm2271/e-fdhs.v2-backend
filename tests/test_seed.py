from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from database.database import AsyncSessionLocal, engine
from database.model import Account, AccountType, Group, Position
from database.seed import seed_database
from routes.auth import verify_password


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
async def test_default_seed_creates_only_master_data_and_preserves_existing_values() -> None:
    first = await seed_database()
    assert first == {"groups_created": 1, "positions_created": 4}

    async with AsyncSessionLocal() as session:
        group = await session.scalar(select(Group).where(Group.name == "320"))
        positions = list(await session.scalars(select(Position)))
        assert group is not None
        assert (group.type, group.parent_id, group.propagate_confirmation) == ("class", None, True)
        assert {position.name for position in positions} == {"一般學生", "班代表", "資訊股長", "教師"}
        assert all(position.permissions == 0 and position.description is None for position in positions)
        assert await session.scalar(select(func.count()).select_from(Account)) == 0

        student_position = next(position for position in positions if position.name == "一般學生")
        student_position.permissions = 123
        await session.commit()

    second = await seed_database()
    assert second == {"groups_skipped": 1, "positions_skipped": 4}
    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(select(Position.permissions).where(Position.name == "一般學生"))
            == 123
        )


@pytest.mark.asyncio
async def test_dev_account_seed_is_idempotent_and_uses_argon2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEED_DEV_PASSWORD", "local-development-password")
    first = await seed_database(dev_account=True)
    assert first == {"groups_created": 2, "positions_created": 4, "accounts_created": 2}

    async with AsyncSessionLocal() as session:
        accounts = list(await session.scalars(select(Account).order_by(Account.account)))
        assert [(account.account, account.account_type) for account in accounts] == [
            ("320", AccountType.STUDENT),
            ("teacher", AccountType.TEACHER),
        ]
        assert all(account.display_name is None for account in accounts)
        assert all(
            [
                await verify_password(
                    account.password_hash, "local-development-password"
                )
                for account in accounts
            ]
        )
        password_hashes = {account.account: account.password_hash for account in accounts}

    second = await seed_database(dev_account=True)
    assert second == {"groups_skipped": 2, "positions_skipped": 4, "accounts_skipped": 2}
    async with AsyncSessionLocal() as session:
        accounts = list(await session.scalars(select(Account).order_by(Account.account)))
        assert {account.account: account.password_hash for account in accounts} == password_hashes


@pytest.mark.asyncio
async def test_dev_account_seed_requires_password_before_writing() -> None:
    with pytest.raises(ValueError, match="SEED_DEV_PASSWORD"):
        await seed_database(dev_account=True)

    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Group)) == 0
        assert await session.scalar(select(func.count()).select_from(Position)) == 0
        assert await session.scalar(select(func.count()).select_from(Account)) == 0
