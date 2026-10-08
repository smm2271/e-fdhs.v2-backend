from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from database.database import AsyncSessionLocal, engine
from database.model import (
    Account, AccountType, Base, Broadcast, BroadcastConfirmation, BroadcastTarget,
    Group, Position, Reply,
)


pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_TEST_CONFIGURED") != "1",
    reason="PostgreSQL integration tests require TEST_DB_HOST, TEST_DB_PORT, TEST_DB_NAME, TEST_DB_USER, and TEST_DB_PASSWORD",
)

def scope_migration():
    return ScriptDirectory.from_config(Config(str(Path(__file__).parents[1] / "alembic.ini"))).get_revision("20261008_0007").module


TABLES = ("broadcasts", "broadcast_targets", "broadcast_confirmations", "replies")
DEADLINE = datetime(2026, 10, 5, 8, 30)  # 16:30 Asia/Taipei, stored as naive UTC.


@pytest.fixture(scope="session", autouse=True)
def migrated_schema() -> None:
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")


@pytest_asyncio.fixture(autouse=True)
async def empty_database() -> None:
    async with engine.begin() as connection:
        await connection.execute(text(
            "TRUNCATE replies, broadcast_confirmations, broadcast_targets, broadcasts, "
            "sessions, permission_overrides, account_roles, accounts, roles, "
            "positions, groups RESTART IDENTITY"
        ))
    yield


@pytest_asyncio.fixture
async def broadcast_data():
    async with AsyncSessionLocal() as session:
        first = Group(type="class", name="Class 201")
        second = Group(type="class", name="Class 202")
        position = Position(name="broadcast-author")
        # Account type imposes no publishing restriction in this data model.
        author = Account(
            account="320021", account_type=AccountType.STUDENT,
            password_hash="already-hashed", group=first, position=position,
        )
        other_author = Account(
            account="320022", account_type=AccountType.STUDENT,
            password_hash="already-hashed", group=first, position=position,
        )
        broadcast = Broadcast(author=author, content="Announcement", ack_deadline_at=DEADLINE)
        broadcast.targets = [BroadcastTarget(group=first), BroadcastTarget(group=second)]
        session.add_all([broadcast, other_author])
        await session.commit()
        yield session, broadcast, first, second, author, other_author


def assert_schema_matches_models(connection) -> None:
    inspector = inspect(connection)
    for name in TABLES:
        model = Base.metadata.tables[name]
        columns = inspector.get_columns(name)
        assert {column["name"] for column in columns} == set(model.columns.keys())
        for column in columns:
            expected = model.c[column["name"]]
            assert column["nullable"] == expected.nullable
            assert column["type"].compile(dialect=postgresql.dialect()) == expected.type.compile(
                dialect=postgresql.dialect()
            )
            expected_default = (
                str(expected.server_default.arg) if expected.server_default is not None else None
            )
            # PostgreSQL reflection adds an explicit cast to string literals.
            actual_default = column["default"]
            if actual_default is not None:
                actual_default = actual_default.replace("'utc'::text", "'utc'")
            assert actual_default == expected_default
        assert inspector.get_pk_constraint(name)["constrained_columns"] == [
            column.name for column in model.primary_key
        ]
        assert {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in inspector.get_foreign_keys(name)
        } == {
            (tuple(element.parent.name for element in fk.elements),
             fk.referred_table.name, tuple(element.column.name for element in fk.elements))
            for fk in model.foreign_key_constraints
        }


@pytest.mark.asyncio
async def test_migrated_broadcast_schema_matches_model_metadata() -> None:
    async with engine.connect() as connection:
        await connection.run_sync(assert_schema_matches_models)


@pytest.mark.asyncio
async def test_broadcast_migration_downgrade_and_upgrade_round_trip() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    migration = ScriptDirectory.from_config(config).get_revision("20261005_0005").module
    target_migration = ScriptDirectory.from_config(config).get_revision("20261006_0006").module

    def round_trip(connection) -> None:
        existing = set(inspect(connection).get_table_names())
        with Operations.context(MigrationContext.configure(connection)):
            scope_migration().downgrade()
            target_migration.downgrade()
            migration.downgrade()
            assert set(inspect(connection).get_table_names()) == existing - set(TABLES)
            migration.upgrade()
            target_migration.upgrade()
            scope_migration().upgrade()
        assert set(inspect(connection).get_table_names()) == existing
        assert_schema_matches_models(connection)

    # PostgreSQL transactional DDL restores the original test schema even on failure.
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(round_trip)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_broadcast_relates_to_multiple_target_groups(broadcast_data) -> None:
    session, broadcast, first, second, author, _ = broadcast_data
    loaded = await session.scalar(select(Broadcast).where(Broadcast.id == broadcast.id).options(
        selectinload(Broadcast.target_groups), selectinload(Broadcast.targets),
        selectinload(Broadcast.author),
    ))
    assert {group.id for group in loaded.target_groups} == {first.id, second.id}
    assert {target.group_id for target in loaded.targets} == {first.id, second.id}
    assert loaded.author.id == author.id
    assert loaded.created_at is not None
    assert loaded.ack_deadline_at == DEADLINE
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(insert(BroadcastTarget).values(
                broadcast_id=broadcast.id, group_id=first.id
            ))


@pytest.mark.asyncio
async def test_confirmation_uniqueness_is_per_group_not_account(broadcast_data) -> None:
    session, broadcast, first, second, author, other_author = broadcast_data
    session.add_all([
        BroadcastConfirmation(broadcast=broadcast, group=first, confirmed_by_account=author),
        BroadcastConfirmation(broadcast=broadcast, group=second, confirmed_by_account=author),
    ])
    await session.commit()
    confirmations = list(await session.scalars(select(BroadcastConfirmation)))
    assert len(confirmations) == 2
    assert all(confirmation.confirmed_at is not None for confirmation in confirmations)
    assert {confirmation.confirmed_by_account_id for confirmation in confirmations} == {author.id}
    # A different actor cannot create a second confirmation for the same group.
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(insert(BroadcastConfirmation).values(
                broadcast_id=broadcast.id, group_id=first.id,
                confirmed_by_account_id=other_author.id,
            ))


@pytest.mark.asyncio
async def test_reply_can_be_direct_or_reference_another_reply(broadcast_data) -> None:
    session, broadcast, first, _, author, _ = broadcast_data
    direct = Reply(target=broadcast.targets[0], author=author, content="Direct reply")
    nested = Reply(
        target=broadcast.targets[0], author=author, content="Nested reply", referenced_reply=direct
    )
    session.add_all([direct, nested])
    await session.commit()
    session.expire_all()
    replies = list(await session.scalars(select(Reply).options(
        selectinload(Reply.referenced_reply), selectinload(Reply.broadcast),
        selectinload(Reply.author),
    )))
    direct = next(reply for reply in replies if reply.ref_id is None)
    nested = next(reply for reply in replies if reply.ref_id is not None)
    assert direct.referenced_reply is None
    assert nested.ref_id == direct.id
    assert nested.referenced_reply is direct
    assert nested.broadcast_id == direct.broadcast_id
    assert nested.author is direct.author
    assert all(reply.created_at is not None for reply in replies)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(insert(Reply).values(
                broadcast_id=direct.broadcast_id, group_id=direct.group_id, author_id=direct.author_id,
                content="Missing reference", ref_id=uuid4(),
            ))


@pytest.mark.asyncio
async def test_broadcast_deadline_is_required(broadcast_data) -> None:
    session, _, _, _, author, _ = broadcast_data
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(insert(Broadcast).values(
                author_id=author.id, content="No deadline", ack_deadline_at=None,
            ))


@pytest.mark.asyncio
async def test_reply_composite_fk_rejects_non_target_group(broadcast_data) -> None:
    session, broadcast, first, _, author, _ = broadcast_data
    non_target = Group(type="class", name="No target")
    session.add(non_target)
    await session.commit()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(insert(Reply).values(
                broadcast_id=broadcast.id, group_id=non_target.id,
                author_id=author.id, content="Wrong target",
            ))
    # A group that is targeted elsewhere still cannot bypass the composite key.
    other = Broadcast(author=author, content="Other", ack_deadline_at=DEADLINE,
                      targets=[BroadcastTarget(group=non_target)])
    session.add(other)
    await session.commit()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(insert(Reply).values(
                broadcast_id=other.id, group_id=first.id,
                author_id=author.id, content="Wrong broadcast",
            ))


@pytest.mark.asyncio
async def test_target_migration_backfills_unique_legacy_target(broadcast_data) -> None:
    _, broadcast, first, _, author, _ = broadcast_data
    broadcast_id, group_id, author_id = broadcast.id, first.id, author.id
    migration = ScriptDirectory.from_config(Config(
        str(Path(__file__).parents[1] / "alembic.ini")
    )).get_revision("20261006_0006").module

    def round_trip(connection):
        with Operations.context(MigrationContext.configure(connection)):
            scope_migration().downgrade()
            migration.downgrade()
            connection.execute(text("DELETE FROM broadcast_targets WHERE broadcast_id = :id AND group_id <> :group"),
                               {"id": broadcast_id, "group": group_id})
            reply_id = uuid4()
            connection.execute(text("INSERT INTO replies (id, broadcast_id, author_id, content) VALUES (:id, :broadcast, :author, 'Legacy')"),
                               {"id": reply_id, "broadcast": broadcast_id, "author": author_id})
            migration.upgrade()
            assert connection.scalar(text("SELECT group_id FROM replies WHERE id = :id"), {"id": reply_id}) == group_id
            scope_migration().upgrade()
            assert_schema_matches_models(connection)
            scope_migration().downgrade()
            migration.downgrade()
            assert connection.scalar(text("SELECT content FROM replies WHERE id = :id"), {"id": reply_id}) == "Legacy"

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(round_trip)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_target_migration_rejects_ambiguous_legacy_replies(broadcast_data) -> None:
    _, broadcast, _, _, author, _ = broadcast_data
    broadcast_id, author_id = broadcast.id, author.id
    migration = ScriptDirectory.from_config(Config(
        str(Path(__file__).parents[1] / "alembic.ini")
    )).get_revision("20261006_0006").module

    def ambiguous(connection):
        with Operations.context(MigrationContext.configure(connection)):
            scope_migration().downgrade()
            migration.downgrade()
            connection.execute(text("INSERT INTO replies (id, broadcast_id, author_id, content) VALUES (:id, :broadcast, :author, 'Ambiguous')"),
                               {"id": uuid4(), "broadcast": broadcast_id, "author": author_id})
            migration.upgrade()

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(Exception, match="Cannot scope legacy replies"):
                await connection.run_sync(ambiguous)
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_database_rejects_cross_thread_parents_and_non_target_confirmations(broadcast_data):
    session, broadcast, first, second, author, _ = broadcast_data
    root = Reply(target=broadcast.targets[0], author=author, content="Root")
    other = Broadcast(author=author, content="Other", ack_deadline_at=DEADLINE,
                      targets=[BroadcastTarget(group=first)])
    session.add_all([root, other])
    await session.commit()
    for broadcast_id, group_id in [(broadcast.id, second.id), (other.id, first.id)]:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(insert(Reply).values(id=uuid4(), broadcast_id=broadcast_id,
                    group_id=group_id, author_id=author.id, content="Cross thread", ref_id=root.id))
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(insert(BroadcastConfirmation).values(broadcast_id=other.id,
                group_id=second.id, confirmed_by_account_id=author.id))
