"""HTTP integration tests use real PostgreSQL and real HttpOnly login cookies."""
import os
from datetime import datetime, timedelta, timezone, date
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from test_auth_routes import migrated_schema, empty_database, api_app
from database.database import AsyncSessionLocal, engine
from database.model import Account, AccountType, Group, Position
from database.service import BroadcastService
from routes.auth import hash_password
from routes.broadcast import week_bounds
from main import create_app

pytestmark = pytest.mark.skipif(os.getenv("POSTGRES_TEST_CONFIGURED") != "1", reason="PostgreSQL test database required")


@pytest_asyncio.fixture
async def actors():
    async with AsyncSessionLocal() as db:
        groups = [Group(type="class", name="201"), Group(type="class", name="202"), Group(type="department", name="Office")]
        positions = [Position(name="ordinary", permissions=0), Position(name="officer", permissions=3), Position(name="representative", permissions=3)]
        password = await hash_password("test-broadcast-password")
        accounts = {}
        for name, kind, group, position in [
            ("author", AccountType.TEACHER, groups[2], positions[0]),
            ("teacher", AccountType.TEACHER, groups[2], positions[0]),
            ("ordinary", AccountType.STUDENT, groups[0], positions[0]),
            ("officer", AccountType.STUDENT, groups[0], positions[1]),
            ("representative", AccountType.STUDENT, groups[0], positions[2]),
            ("outsider", AccountType.STUDENT, groups[1], positions[1]),
        ]:
            accounts[name] = Account(account=name, account_type=kind, group=group, position=position,
                                     password_hash=password, display_name=name, is_active=True)
        db.add_all(accounts.values())
        await db.commit()
        return [g.id for g in groups], {name: a.id for name, a in accounts.items()}


async def login(client, name):
    kind = "teacher" if name in ("author", "teacher") else "student"
    role = name if name in ("ordinary", "representative") else "officer"
    payload = {"account_type": kind, "account": name, "password": "test-broadcast-password"}
    if kind == "student":
        payload["position_name"] = role
    response = await client.post("/auth/login", json=payload)
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_real_cookie_officer_lifecycle_and_readonly_student(api_app, actors):
    groups, ids = actors
    async with AsyncClient(transport=ASGITransport(app=api_app), base_url="https://test") as client:
        assert (await client.get("/broadcasts")).status_code == 401
        await login(client, "author")
        published = await client.post("/broadcasts", json={"content": "Notice", "target_group_ids": [str(groups[0])]})
        assert published.status_code == 201
        broadcast = published.json()
        bid = broadcast["id"]
        assert broadcast["created_at"].endswith("Z")
        assert broadcast["targets"][0]["can_reply"]
        assert not broadcast["targets"][0]["can_confirm"]
        async with AsyncSessionLocal() as db:
            historical = await BroadcastService(db, clock=lambda: datetime.now(timezone.utc) - timedelta(days=8)).create(
                account_id=ids["author"], content="History", target_group_ids=[groups[0]])
            old_id = str(historical.id)
        await login(client, "officer")
        feed = await client.get("/broadcasts")
        assert feed.status_code == 200
        assert [b["id"] for b in feed.json()["items"]] == [bid]
        assert feed.json()["current_user"]["can_confirm"]
        assert feed.json()["pending_count"] == 1
        assert (await client.get("/broadcasts/pending-count")).json()["pending_count"] == 1
        assert len((await client.get("/broadcasts", params={"history": "true"})).json()["items"]) == 2
        reply = await client.post(f"/broadcasts/{bid}/replies", json={"content": "Received"})
        assert reply.status_code == 201
        assert reply.json()["targets"][0]["confirmation"] is None
        parent = reply.json()["targets"][0]["replies"][0]["id"]
        for depth in range(3):
            reply = await client.post(f"/broadcasts/{bid}/replies", json={"content": f"Level {depth}", "ref_id": parent})
            assert reply.status_code == 201
            node = reply.json()["targets"][0]["replies"][0]
            for _ in range(depth + 1):
                node = node["replies"][0]
            parent = node["id"]
        assert reply.json()["targets"][0]["reply_count"] == 4
        assert (await client.post(f"/broadcasts/{old_id}/replies", json={"content": "Historical"})).status_code == 201
        confirmed = await client.post(f"/broadcasts/{bid}/confirmations", json={})
        assert confirmed.status_code == 201
        original = confirmed.json()["targets"][0]["confirmation"]
        assert original["confirmed_by_account_id"] == str(ids["officer"])
        assert (await client.get("/broadcasts/pending-count")).json()["pending_count"] == 0
        await login(client, "representative")
        assert (await client.post(f"/broadcasts/{bid}/confirmations", json={})).status_code == 409
        assert (await client.get(f"/broadcasts/{bid}")).json()["targets"][0]["confirmation"] == original
        await login(client, "ordinary")
        feed = (await client.get("/broadcasts")).json()
        assert not feed["current_user"]["can_confirm"] and not feed["current_user"]["can_reply"]
        assert len((await client.get(f"/broadcasts/{bid}/replies")).json()) == 1
        assert (await client.post(f"/broadcasts/{bid}/confirmations", json={})).status_code == 403
        assert (await client.post(f"/broadcasts/{bid}/replies", json={"content": "Denied"})).status_code == 403
        assert (await client.post("/broadcasts", json={"content": "Denied", "target_group_ids": [str(groups[0])]})).status_code == 403


@pytest.mark.asyncio
async def test_http_target_scope_and_parent_validation(api_app, actors):
    groups, ids = actors
    async with AsyncClient(transport=ASGITransport(app=api_app), base_url="https://test") as client:
        await login(client, "author")
        payload = {"content": "Notice", "target_group_ids": [str(groups[0]), str(groups[1])]}
        bid = (await client.post("/broadcasts", json=payload)).json()["id"]
        other = (await client.post("/broadcasts", json=payload)).json()["id"]
        assert (await client.get(f"/broadcasts/{bid}/replies")).status_code == 422
        assert (await client.post(f"/broadcasts/{bid}/replies", json={"content": "Missing target"})).status_code == 422
        root = (await client.post(f"/broadcasts/{bid}/replies", json={"content": "Root", "group_id": str(groups[0])})).json()["targets"][0]["replies"][0]["id"]
        for target_bid, group in [(bid, groups[1]), (other, groups[0])]:
            response = await client.post(f"/broadcasts/{target_bid}/replies", json={"content": "Cross thread", "group_id": str(group), "ref_id": root})
            assert response.status_code == 422
        assert (await client.post(f"/broadcasts/{bid}/replies", json={"content": "Missing parent", "group_id": str(groups[0]), "ref_id": str(uuid4())})).status_code == 404
        await login(client, "teacher")
        assert (await client.get(f"/broadcasts/{bid}/replies", params={"group_id": str(groups[0])})).status_code == 200
        assert (await client.post(f"/broadcasts/{bid}/replies", json={"content": "Denied", "group_id": str(groups[0])})).status_code == 403
        assert all(not t["can_reply"] for t in (await client.get(f"/broadcasts/{bid}")).json()["targets"])
        await login(client, "officer")
        assert len((await client.get(f"/broadcasts/{bid}")).json()["targets"]) == 1
        for path in ["/broadcasts", f"/broadcasts/{bid}", f"/broadcasts/{bid}/replies"]:
            assert (await client.get(path, params={"group_id": str(groups[1])})).status_code == 403
        for suffix in ["confirmations", "replies"]:
            assert (await client.post(f"/broadcasts/{bid}/{suffix}", json={"content": "Denied", "group_id": str(groups[1])})).status_code == 403
        await login(client, "author")
        hidden = (await client.post("/broadcasts", json={"content": "Hidden", "target_group_ids": [str(groups[1])]})).json()["id"]
        await login(client, "ordinary")
        assert (await client.get(f"/broadcasts/{hidden}")).status_code == 404
        assert (await client.get(f"/broadcasts/{hidden}/replies")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("targets,content,expected", [([], "Notice", 422), (["missing"], "Notice", 404), (["office"], "Notice", 422), (["class"], "   ", 422)])
async def test_http_publish_validation(api_app, actors, targets, content, expected):
    groups, _ = actors
    values = {"missing": uuid4(), "office": groups[2], "class": groups[0]}
    async with AsyncClient(transport=ASGITransport(app=api_app), base_url="https://test") as client:
        await login(client, "author")
        response = await client.post("/broadcasts", json={"content": content, "target_group_ids": [str(values[t]) for t in targets]})
        assert response.status_code == expected
        assert (await client.get("/broadcasts")).json()["items"] == []


def test_openapi_and_taipei_display_week():
    schema = create_app().openapi()
    assert "/broadcasts/pending-count" in schema["paths"]
    assert schema["paths"]["/broadcasts/{broadcast_id}/replies"]["post"]["responses"]["201"]
    assert week_bounds(date(2026, 10, 11)) == (datetime(2026, 10, 4, 16), datetime(2026, 10, 11, 16))
    assert week_bounds(date(2026, 10, 12)) == (datetime(2026, 10, 11, 16), datetime(2026, 10, 18, 16))


@pytest.mark.asyncio
async def test_feed_queries_are_batched_for_many_broadcasts(api_app, actors):
    groups, ids = actors
    async with AsyncSessionLocal() as db:
        for index in range(20):
            await BroadcastService(db).create(account_id=ids["author"], content=f"Notice {index}", target_group_ids=groups[:2])
    async with AsyncClient(transport=ASGITransport(app=api_app), base_url="https://test") as client:
        await login(client, "teacher")
        statements = []
        def count_query(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(engine.sync_engine, "before_cursor_execute", count_query)
        try:
            response = await client.get("/broadcasts")
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count_query)
        assert response.status_code == 200
        assert len(response.json()["items"]) == 20
        assert all(len(item["targets"]) == 2 for item in response.json()["items"])
        # Includes real cookie authentication and effective-permission queries.
        assert len(statements) < 25
