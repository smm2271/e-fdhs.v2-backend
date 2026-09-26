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
from database.model import AccountType, Session
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
            account_type=AccountType.STUDENT,
            position_id=position.id,
            password_hash=await hash_password("old-secret"),
            group_id=group.id,
            display_name="Original",
        )

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        invalid_login = await client.post(
            "/auth/login",
            json={
                "account_type": "student",
                "account": "320201",
                "position_name": "student",
                "password": "incorrect",
            },
        )
        assert invalid_login.status_code == 401

        login = await client.post(
            "/auth/login",
            json={
                "account_type": "student",
                "account": "320201",
                "position_name": "student",
                "password": "old-secret",
            },
        )
        assert login.status_code == 200
        assert "access_token" not in login.json()
        token = login.cookies.get("__Host-session")
        assert token is not None
        assert token != hash_token(token)
        cookie_header = login.headers["set-cookie"].lower()
        assert "httponly" in cookie_header
        assert "secure" in cookie_header
        assert "samesite=lax" in cookie_header

        profile = await client.get("/users/me")
        assert profile.status_code == 200
        assert profile.json()["display_name"] == "Original"
        assert profile.json()["position_name"] == "student"
        assert profile.json()["group_name"] == "Class 201"
        assert "position_id" not in profile.json()
        assert "group_id" not in profile.json()
        assert "password_hash" not in profile.json()

        updated_profile = await client.patch(
            "/users/me", json={"display_name": "Updated"}
        )
        assert updated_profile.status_code == 200
        assert updated_profile.json()["display_name"] == "Updated"

        short_password = await client.patch(
            "/users/me/password",
            json={"current_password": "old-secret", "new_password": "short"},
        )
        assert short_password.status_code == 422

        changed_password = await client.patch(
            "/users/me/password",
            json={"current_password": "old-secret", "new_password": "new-secret-long"},
        )
        assert changed_password.status_code == 204
        assert (await client.get("/users/me")).status_code == 401

        old_password_login = await client.post(
            "/auth/login",
            json={
                "account_type": "student",
                "account": "320201",
                "position_name": "student",
                "password": "old-secret",
            },
        )
        assert old_password_login.status_code == 401
        new_password_login = await client.post(
            "/auth/login",
            json={
                "account_type": "student",
                "account": "320201",
                "position_name": "student",
                "password": "new-secret-long",
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


@pytest.mark.asyncio
async def test_student_and_teacher_login_identity_rules(api_app) -> None:
    async with AsyncSessionLocal() as session:
        group = await GroupService(session).create(
            group_type="department", name="Academic Affairs"
        )
        student_position = await PositionService(session).create(
            name="general-student"
        )
        wrong_position = await PositionService(session).create(name="class-monitor")
        teacher_position = await PositionService(session).create(name="teacher")
        await AccountService(session).create(
            account="s399",
            account_type=AccountType.STUDENT,
            position_id=student_position.id,
            password_hash=await hash_password("student-secret"),
            group_id=group.id,
        )
        await AccountService(session).create(
            account="t001",
            account_type=AccountType.TEACHER,
            position_id=teacher_position.id,
            password_hash=await hash_password("teacher-secret"),
            group_id=group.id,
        )

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        student_login = await client.post(
            "/auth/login",
            json={
                "account_type": "student",
                "account": "s399",
                "position_name": student_position.name,
                "password": "student-secret",
            },
        )
        assert student_login.status_code == 200

        missing_position = await client.post(
            "/auth/login",
            json={
                "account_type": "student",
                "account": "s399",
                "password": "student-secret",
            },
        )
        assert missing_position.status_code == 422

        wrong_student_position = await client.post(
            "/auth/login",
            json={
                "account_type": "student",
                "account": "s399",
                "position_name": wrong_position.name,
                "password": "student-secret",
            },
        )
        assert wrong_student_position.status_code == 401

        teacher_login = await client.post(
            "/auth/login",
            json={
                "account_type": "teacher",
                "account": "t001",
                "password": "teacher-secret",
            },
        )
        assert teacher_login.status_code == 200

        teacher_as_student = await client.post(
            "/auth/login",
            json={
                "account_type": "student",
                "account": "t001",
                "position_name": teacher_position.name,
                "password": "teacher-secret",
            },
        )
        assert teacher_as_student.status_code == 401
