from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select, text

from database.database import AsyncSessionLocal, engine
from database.model import Account, AccountType, Broadcast, BroadcastConfirmation, BroadcastTarget, Group, Position, Reply
from database.permissions import Permission
from database.service import (
    AccountService, AuthorizationError, BroadcastService, BroadcastConfirmationService,
    ConflictError, NotFoundError, PermissionOverrideService, ReplyService, RoleService, ValidationError,
)

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_TEST_CONFIGURED") != "1", reason="PostgreSQL test database required",
)
CREATED = datetime(2026, 10, 5, 7, 20)  # 15:20 Taipei; deadline tomorrow.


@pytest.fixture(scope="session", autouse=True)
def migrated_schema():
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")


@pytest_asyncio.fixture(autouse=True)
async def empty_database():
    async with engine.begin() as connection:
        await connection.execute(text(
            "TRUNCATE replies, broadcast_confirmations, broadcast_targets, broadcasts, "
            "sessions, permission_overrides, account_roles, accounts, roles, positions, groups"
        ))
    yield


@pytest_asyncio.fixture
async def data():
    async with AsyncSessionLocal() as session:
        first, second = Group(type="class", name="398"), Group(type="class", name="399")
        office = Group(type="department", name="Teachers")
        ordinary = Position(name="any-ordinary-name", permissions=0)
        officer = Position(name="any-officer-name", permissions=int(
            Permission.CONFIRM_BROADCAST | Permission.REPLY_BROADCAST
        ))
        representative = Position(name="any-representative-name", permissions=officer.permissions)
        actors = {}
        for name, kind, group, position in [
            ("author", AccountType.TEACHER, office, ordinary),
            ("teacher", AccountType.TEACHER, office, officer),
            ("ordinary", AccountType.STUDENT, first, ordinary),
            ("officer", AccountType.STUDENT, first, officer),
            ("representative", AccountType.STUDENT, first, representative),
            ("outsider", AccountType.STUDENT, second, officer),
        ]:
            actors[name] = Account(account=name, account_type=kind, group=group,
                                   position=position, password_hash="hashed", is_active=True)
        session.add_all(actors.values())
        await session.commit()
        yield session, first.id, second.id, office.id, {name: actor.id for name, actor in actors.items()}


async def publish(data, targets=None):
    session, first, second, _, actors = data
    return await BroadcastService(session, clock=lambda: CREATED).create(
        account_id=actors["author"], content="Announcement",
        target_group_ids=[first, second] if targets is None else targets,
    )


@pytest.mark.asyncio
async def test_creation_deduplicates_targets_and_calculates_deadline(data):
    session, first, second, _, actors = data
    broadcast = await publish(data, [first, second, first])
    assert broadcast.author_id == actors["author"]
    assert broadcast.created_at == CREATED
    assert broadcast.ack_deadline_at == datetime(2026, 10, 6, 8, 30)
    await session.refresh(broadcast, ["targets"])
    assert {target.group_id for target in broadcast.targets} == {first, second}
    assert len(broadcast.targets) == 2


@pytest.mark.asyncio
async def test_target_constraint_failure_rolls_back_entire_creation(data):
    session, _, second, _, _ = data

    def invalidate_target(sync_session, flush_context, instances):
        # Force a target FK failure after service validation, as can happen when
        # a target is concurrently removed. The broadcast must not survive.
        for entity in sync_session.new:
            if isinstance(entity, BroadcastTarget) and entity.group_id == second:
                entity.group_id = uuid4()

    event.listen(session.sync_session, "before_flush", invalidate_target, once=True)
    with pytest.raises(ConflictError):
        await publish(data)
    assert await session.scalar(select(func.count()).select_from(Broadcast)) == 0
    assert await session.scalar(select(func.count()).select_from(BroadcastTarget)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("case,error", [
    ("student", AuthorizationError), ("empty", ValidationError),
    ("missing", NotFoundError), ("office", ValidationError), ("inactive", AuthorizationError),
])
async def test_invalid_creation_leaves_no_broadcast(data, case, error):
    session, first, _, office, actors = data
    account_id = actors["ordinary"] if case == "student" else actors["author"]
    targets = {"empty": [], "missing": [first, uuid4()], "office": [office]}.get(case, [first])
    if case == "inactive":
        await AccountService(session).update(account_id, is_active=False)
    with pytest.raises(error):
        await BroadcastService(session).create(account_id=account_id, content="Invalid", target_group_ids=targets)
    assert await session.scalar(select(func.count()).select_from(Broadcast)) == 0


@pytest.mark.asyncio
async def test_broadcast_read_scope_and_teacher_group_filter(data):
    session, first, second, _, actors = data
    broadcast = await publish(data, [first])
    service = BroadcastService(session)
    assert (await service.get(broadcast.id, account_id=actors["teacher"])).id == broadcast.id
    assert (await service.get(broadcast.id, account_id=actors["ordinary"])).id == broadcast.id
    assert len(await service.list(account_id=actors["teacher"], group_id=first)) == 1
    assert await service.list(account_id=actors["teacher"], group_id=second) == []
    assert await service.list(account_id=actors["outsider"]) == []
    with pytest.raises(NotFoundError):
        await service.get(broadcast.id, account_id=actors["outsider"])
    with pytest.raises(AuthorizationError):
        await service.list(account_id=actors["outsider"], group_id=first)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,allowed", [
    ("ordinary", False), ("officer", True), ("representative", True),
    ("outsider", False), ("author", False), ("teacher", False),
])
async def test_confirmation_authorization(data, name, allowed):
    session, first, _, _, actors = data
    broadcast = await publish(data)
    service = BroadcastConfirmationService(session, clock=lambda: CREATED + timedelta(minutes=1))
    if allowed:
        confirmation = await service.confirm(broadcast.id, first, account_id=actors[name])
        assert confirmation.confirmed_by_account_id == actors[name]
        assert await service.status(broadcast.id, first, account_id=actors[name]) == "confirmed_on_time"
        assert await session.scalar(select(func.count()).select_from(Reply)) == 0
    else:
        with pytest.raises(AuthorizationError):
            await service.confirm(broadcast.id, first, account_id=actors[name])


@pytest.mark.asyncio
async def test_late_confirmation_and_duplicate_preserve_first_record(data):
    session, first, _, _, actors = data
    broadcast = await publish(data)
    broadcast_id = broadcast.id
    late = CREATED + timedelta(days=14)
    service = BroadcastConfirmationService(session, clock=lambda: late)
    assert await service.status(broadcast_id, first, account_id=actors["officer"]) == "overdue"
    original = await service.confirm(broadcast_id, first, account_id=actors["officer"])
    original_time = original.confirmed_at
    assert await service.status(broadcast_id, first, account_id=actors["representative"]) == "confirmed_late"
    with pytest.raises(ConflictError):
        await service.confirm(broadcast_id, first, account_id=actors["representative"])
    loaded = await service.get(broadcast_id, first, account_id=actors["teacher"])
    assert (loaded.confirmed_at, loaded.confirmed_by_account_id) == (original_time, actors["officer"])
    assert await session.scalar(select(func.count()).select_from(BroadcastConfirmation)) == 1


@pytest.mark.asyncio
async def test_concurrent_confirmations_have_one_winner(data):
    session, first, _, _, actors = data
    broadcast_id = (await publish(data)).id
    async def confirm(name, stamp):
        async with AsyncSessionLocal() as other:
            service = BroadcastConfirmationService(other, clock=lambda: stamp)
            try:
                result = await service.confirm(broadcast_id, first, account_id=actors[name])
                return (result.confirmed_by_account_id, result.confirmed_at)
            except ConflictError:
                # The failed session remains usable after the service rolls it back.
                assert await other.scalar(select(func.count()).select_from(BroadcastConfirmation)) == 1
                return None
    results = await asyncio.gather(confirm("officer", CREATED),
                                   confirm("representative", CREATED + timedelta(seconds=1)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    stored = await session.get(BroadcastConfirmation, (broadcast_id, first))
    assert (stored.confirmed_by_account_id, stored.confirmed_at) == winners[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,allowed", [
    ("ordinary", False), ("officer", True), ("representative", True),
    ("outsider", False), ("author", True), ("teacher", False),
])
async def test_reply_authorization_and_no_confirmation_side_effect(data, name, allowed):
    session, first, second, _, actors = data
    broadcast_id = (await publish(data)).id
    service = ReplyService(session, clock=lambda: CREATED + timedelta(days=30))
    if allowed:
        reply = await service.create(broadcast_id, first, account_id=actors[name], content="Historical reply")
        assert reply.group_id == first
        if name == "author":
            await service.create(broadcast_id, second, account_id=actors[name], content="Other target")
    else:
        with pytest.raises(AuthorizationError):
            await service.create(broadcast_id, first, account_id=actors[name], content="Denied")
    assert await session.scalar(select(func.count()).select_from(BroadcastConfirmation)) == 0


@pytest.mark.asyncio
async def test_reply_tree_parent_invariants_and_read_scope(data):
    session, first, second, _, actors = data
    broadcast_id = (await publish(data)).id
    other_id = (await publish(data)).id
    service = ReplyService(session)
    root = await service.create(broadcast_id, first, account_id=actors["officer"], content="Root")
    child = await service.create(broadcast_id, first, account_id=actors["author"], content="Child", ref_id=root.id)
    assert child.ref_id == root.id
    for scope in [(broadcast_id, second), (other_id, first)]:
        with pytest.raises(ValidationError):
            await service.create(*scope, account_id=actors["author"], content="Cross scope", ref_id=root.id)
    with pytest.raises(NotFoundError):
        await service.create(broadcast_id, first, account_id=actors["author"], content="Missing", ref_id=uuid4())
    assert {node.id for node in await service.list_thread(broadcast_id, first, account_id=actors["teacher"])} == {root.id, child.id}
    assert len(await service.list_thread(broadcast_id, first, account_id=actors["ordinary"])) == 2
    assert await service.list_thread(broadcast_id, second, account_id=actors["outsider"]) == []
    with pytest.raises(AuthorizationError):
        await service.list_thread(broadcast_id, first, account_id=actors["outsider"])
    with pytest.raises(AuthorizationError):
        await service.get(root.id, account_id=actors["outsider"])
    assert (await service.get(root.id, account_id=actors["teacher"])).id == root.id


@pytest.mark.asyncio
async def test_permissions_use_roles_and_active_overrides(data):
    session, first, _, _, actors = data
    broadcast_id = (await publish(data)).id
    role = await RoleService(session).create(name="broadcast-replier", permissions=int(Permission.REPLY_BROADCAST))
    await AccountService(session).assign_role(actors["ordinary"], role.id)
    reply_service = ReplyService(session, clock=lambda: CREATED)
    await reply_service.create(broadcast_id, first, account_id=actors["ordinary"], content="Role allows")
    await PermissionOverrideService(session).create(
        account_id=actors["ordinary"], deny_permissions=int(Permission.REPLY_BROADCAST),
        starts_at=CREATED - timedelta(seconds=1), expires_at=CREATED + timedelta(seconds=1),
    )
    with pytest.raises(AuthorizationError):
        await reply_service.create(broadcast_id, first, account_id=actors["ordinary"], content="Deny wins")
    await ReplyService(session, clock=lambda: CREATED + timedelta(seconds=2)).create(
        broadcast_id, first, account_id=actors["ordinary"], content="Deny expired",
    )


@pytest.mark.asyncio
async def test_non_target_confirmation_and_reply_are_rejected(data):
    session, first, second, _, actors = data
    broadcast_id = (await publish(data, [first])).id
    with pytest.raises(NotFoundError):
        await BroadcastConfirmationService(session).confirm(broadcast_id, second, account_id=actors["outsider"])
    with pytest.raises(NotFoundError):
        await ReplyService(session).create(broadcast_id, second, account_id=actors["author"], content="No target")
    with pytest.raises(AuthorizationError):
        await BroadcastConfirmationService(session).get(broadcast_id, first, account_id=actors["outsider"])


@pytest.mark.asyncio
async def test_confirmation_permission_override_and_pending_state(data):
    session, first, _, _, actors = data
    broadcast_id = (await publish(data)).id
    service = BroadcastConfirmationService(session, clock=lambda: CREATED)
    assert await service.status(broadcast_id, first, account_id=actors["ordinary"]) == "pending"
    await PermissionOverrideService(session).create(
        account_id=actors["ordinary"], allow_permissions=int(Permission.CONFIRM_BROADCAST),
    )
    await service.confirm(broadcast_id, first, account_id=actors["ordinary"])
    other_id = (await publish(data)).id
    await PermissionOverrideService(session).create(
        account_id=actors["officer"], deny_permissions=int(Permission.CONFIRM_BROADCAST),
    )
    with pytest.raises(AuthorizationError):
        await service.confirm(other_id, first, account_id=actors["officer"])
