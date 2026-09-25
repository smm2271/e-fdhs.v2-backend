from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from database.database import AsyncSessionLocal, engine, get_session
from database.model import Session
from database.service import AccountService, GroupService, PositionService, SessionService
from main import create_app
from routes.auth import hash_password, hash_token


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


@pytest.fixture
def api_app():
    app = create_app()

    async def override_session():
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_login_profile_update_and_password_change_revoke_all_sessions(api_app) -> None:
    async with AsyncSessionLocal() as session:
        group = await GroupService(session).create(group_type="class", name="Class 201")
        position = await PositionService(session).create(name="student")
        account = await AccountService(session).create(
            account="320201",
            position_id=position.id,
            password_hash=await hash_password("old-secret"),
            group_id=group.id,
            display_name="Original",
        )

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        invalid_login = await client.post(
            "/auth/login",
            json={
                "account": "320201",
                "position_name": "student",
                "password": "incorrect",
            },
        )
        assert invalid_login.status_code == 401

        login = await client.post(
            "/auth/login",
            json={
                "account": "320201",
                "position_name": "student",
                "password": "old-secret",
            },
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        assert token != hash_token(token)
        headers = {"Authorization": f"Bearer {token}"}

        profile = await client.get("/users/me", headers=headers)
        assert profile.status_code == 200
        assert profile.json()["display_name"] == "Original"
        assert profile.json()["position_name"] == "student"
        assert profile.json()["group_name"] == "Class 201"
        assert "position_id" not in profile.json()
        assert "group_id" not in profile.json()
        assert "password_hash" not in profile.json()

        updated_profile = await client.patch(
            "/users/me", json={"display_name": "Updated"}, headers=headers
        )
        assert updated_profile.status_code == 200
        assert updated_profile.json()["display_name"] == "Updated"

        short_password = await client.patch(
            "/users/me/password",
            json={"current_password": "old-secret", "new_password": "short"},
            headers=headers,
        )
        assert short_password.status_code == 422

        changed_password = await client.patch(
            "/users/me/password",
            json={"current_password": "old-secret", "new_password": "new-secret"},
            headers=headers,
        )
        assert changed_password.status_code == 204
        assert (await client.get("/users/me", headers=headers)).status_code == 401

        old_password_login = await client.post(
            "/auth/login",
            json={
                "account": "320201",
                "position_name": "student",
                "password": "old-secret",
            },
        )
        assert old_password_login.status_code == 401
        new_password_login = await client.post(
            "/auth/login",
            json={
                "account": "320201",
                "position_name": "student",
                "password": "new-secret",
            },
        )
        assert new_password_login.status_code == 200

    async with AsyncSessionLocal() as session:
        persisted_session = await session.scalar(
            select(Session).where(Session.token_hash == hash_token(token))
        )
        assert persisted_session is not None
        assert persisted_session.token_hash != token
        assert persisted_session.revoked_at is not None
