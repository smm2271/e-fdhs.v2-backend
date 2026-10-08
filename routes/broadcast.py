"""Session-authenticated, target-scoped broadcast API."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.database import get_session
from database.model import Account, AccountType, Broadcast, BroadcastTarget, BroadcastConfirmation, Reply
from database.broadcast_cycle import DOMAIN_TIMEZONE, ConfirmationState, confirmation_state, utc_naive
from database.permissions import Permission
from database.service import (AccountService, BroadcastService, BroadcastConfirmationService,
                              ReplyService, NotFoundError)
from routes.auth import AuthenticatedPrincipal, get_current_principal

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])
DB = Annotated[AsyncSession, Depends(get_session)]
Principal = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]


class ContentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)

    @field_validator("content")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Content must not be empty")
        return value


class PublishRequest(ContentRequest):
    target_group_ids: list[UUID] = Field(min_length=1, max_length=500)


class ReplyRequest(ContentRequest):
    group_id: UUID | None = None
    ref_id: UUID | None = None


class ConfirmRequest(BaseModel):
    group_id: UUID | None = None


class Capabilities(BaseModel):
    can_confirm: bool
    can_reply: bool


class UserResponse(Capabilities):
    id: UUID
    name: str
    role: str
    can_publish: bool


class ConfirmationResponse(BaseModel):
    confirmed_by_account_id: UUID
    confirmed_by: str
    role: str
    confirmed_at: datetime


class ReplyResponse(BaseModel):
    id: UUID
    ref_id: UUID | None
    author_id: UUID
    author_name: str
    role: str
    content: str
    created_at: datetime
    replies: list["ReplyResponse"] = Field(default_factory=list)


class TargetResponse(Capabilities):
    group_id: UUID
    group_name: str
    confirmation_state: ConfirmationState
    confirmation: ConfirmationResponse | None
    reply_count: int
    replies: list[ReplyResponse]


class BroadcastResponse(BaseModel):
    id: UUID
    author_id: UUID
    sender_name: str
    content: str
    created_at: datetime
    ack_deadline_at: datetime
    targets: list[TargetResponse]


class FeedResponse(BaseModel):
    items: list[BroadcastResponse]
    current_user: UserResponse
    pending_count: int


class PendingResponse(BaseModel):
    pending_count: int


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc)


def week_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    local_day = day or datetime.now(DOMAIN_TIMEZONE).date()
    monday = local_day - timedelta(days=local_day.weekday())
    start = datetime.combine(monday, time(), DOMAIN_TIMEZONE)
    return utc_naive(start), utc_naive(start + timedelta(days=7))


def scoped_group(principal: AuthenticatedPrincipal, group_id: UUID | None) -> UUID:
    account = principal.account
    if account.account_type == AccountType.STUDENT:
        if group_id is not None and group_id != account.group_id:
            raise HTTPException(403, "Cannot access another group's broadcast thread")
        return account.group_id
    if group_id is None:
        raise HTTPException(422, "Teachers must specify a target group_id")
    return group_id


async def permissions(db: AsyncSession, principal: AuthenticatedPrincipal) -> int:
    return await AccountService(db).effective_permissions(principal.account.id)


async def pending_count(db: AsyncSession, principal: AuthenticatedPrincipal, mask: int) -> int:
    if principal.account.account_type != AccountType.STUDENT or not mask & Permission.CONFIRM_BROADCAST:
        return 0
    start, end = week_bounds()
    return await db.scalar(select(func.count()).select_from(BroadcastTarget)
        .join(Broadcast, Broadcast.id == BroadcastTarget.broadcast_id)
        .outerjoin(BroadcastConfirmation, (BroadcastConfirmation.broadcast_id == BroadcastTarget.broadcast_id)
                   & (BroadcastConfirmation.group_id == BroadcastTarget.group_id))
        .where(BroadcastTarget.group_id == principal.account.group_id,
               Broadcast.created_at >= start, Broadcast.created_at < end,
               BroadcastConfirmation.broadcast_id.is_(None)))


async def present(
    db: AsyncSession, principal: AuthenticatedPrincipal, ids: list[UUID],
    mask: int, group_id: UUID | None = None,
) -> list[BroadcastResponse]:
    if not ids:
        return []
    records = list(await db.scalars(select(Broadcast).where(Broadcast.id.in_(ids)).options(
        selectinload(Broadcast.author), selectinload(Broadcast.targets).selectinload(BroadcastTarget.group),
        selectinload(Broadcast.confirmations).selectinload(BroadcastConfirmation.confirmed_by_account)
            .selectinload(Account.position))))
    student = principal.account.account_type == AccountType.STUDENT
    scope = principal.account.group_id if student else group_id
    query = select(Reply).where(Reply.broadcast_id.in_(ids)).options(
        selectinload(Reply.author).selectinload(Account.position)).order_by(Reply.created_at, Reply.id)
    if scope is not None:
        query = query.where(Reply.group_id == scope)
    threads = {}
    for reply in await db.scalars(query):
        threads.setdefault((reply.broadcast_id, reply.group_id), []).append(reply)
    result = {}
    now = datetime.now(timezone.utc)
    for broadcast in records:
        confirmations = {c.group_id: c for c in broadcast.confirmations}
        targets = []
        for target in sorted(broadcast.targets, key=lambda t: (t.group.name, str(t.group_id))):
            if scope is not None and target.group_id != scope:
                continue
            raw = threads.get((broadcast.id, target.group_id), [])
            nodes = {r.id: ReplyResponse(id=r.id, ref_id=r.ref_id, author_id=r.author_id,
                author_name=r.author.display_name or r.author.account, role=r.author.position.name,
                content=r.content, created_at=aware(r.created_at)) for r in raw}
            roots = []
            for r in raw:
                if r.ref_id is None:
                    roots.append(nodes[r.id])
                else:
                    nodes[r.ref_id].replies.append(nodes[r.id])
            confirmation = confirmations.get(target.group_id)
            confirmed = None if confirmation is None else ConfirmationResponse(
                confirmed_by_account_id=confirmation.confirmed_by_account_id,
                confirmed_by=confirmation.confirmed_by_account.display_name or confirmation.confirmed_by_account.account,
                role=confirmation.confirmed_by_account.position.name, confirmed_at=aware(confirmation.confirmed_at))
            targets.append(TargetResponse(group_id=target.group_id, group_name=target.group.name,
                confirmation_state=confirmation_state(broadcast.ack_deadline_at,
                    confirmed_at=confirmation.confirmed_at if confirmation else None, now=now),
                confirmation=confirmed, reply_count=len(raw), replies=roots,
                can_confirm=bool(student and mask & Permission.CONFIRM_BROADCAST),
                can_reply=bool(mask & Permission.REPLY_BROADCAST) if student
                    else broadcast.author_id == principal.account.id))
        result[broadcast.id] = BroadcastResponse(id=broadcast.id, author_id=broadcast.author_id,
            sender_name=broadcast.author.display_name or broadcast.author.account,
            content=broadcast.content, created_at=aware(broadcast.created_at),
            ack_deadline_at=aware(broadcast.ack_deadline_at), targets=targets)
    return [result[i] for i in ids]


async def detail(
    db: AsyncSession, principal: AuthenticatedPrincipal, broadcast_id: UUID,
    group_id: UUID | None = None,
) -> BroadcastResponse:
    await BroadcastService(db).get(broadcast_id, account_id=principal.account.id)
    if group_id is not None:
        group_id = scoped_group(principal, group_id)
        if await db.get(BroadcastTarget, (broadcast_id, group_id)) is None:
            raise NotFoundError("Broadcast target was not found")
    return (await present(db, principal, [broadcast_id], await permissions(db, principal), group_id))[0]


@router.get("/pending-count", response_model=PendingResponse)
async def read_pending(db: DB, principal: Principal):
    return PendingResponse(pending_count=await pending_count(db, principal, await permissions(db, principal)))


@router.get("", response_model=FeedResponse)
async def list_broadcasts(db: DB, principal: Principal, group_id: UUID | None = None,
                          week: date | None = None, history: bool = False,
                          limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    if principal.account.account_type == AccountType.STUDENT:
        group_id = scoped_group(principal, group_id)
    query = select(Broadcast.id)
    if group_id is not None:
        query = query.join(BroadcastTarget).where(BroadcastTarget.group_id == group_id)
    if not history:
        start, end = week_bounds(week)
        query = query.where(Broadcast.created_at >= start, Broadcast.created_at < end)
    ids = list(await db.scalars(query.order_by(Broadcast.created_at.desc(), Broadcast.id).limit(limit).offset(offset)))
    mask = await permissions(db, principal)
    student = principal.account.account_type == AccountType.STUDENT
    return FeedResponse(items=await present(db, principal, ids, mask, group_id),
        current_user=UserResponse(id=principal.account.id,
            name=principal.account.display_name or principal.account.account, role=principal.position_name,
            can_publish=not student, can_confirm=bool(student and mask & Permission.CONFIRM_BROADCAST),
            can_reply=bool(student and mask & Permission.REPLY_BROADCAST)),
        pending_count=await pending_count(db, principal, mask))


@router.post("", response_model=BroadcastResponse, status_code=201)
async def publish(payload: PublishRequest, db: DB, principal: Principal):
    record = await BroadcastService(db).create(account_id=principal.account.id,
        content=payload.content, target_group_ids=payload.target_group_ids)
    return await detail(db, principal, record.id)


@router.get("/{broadcast_id}", response_model=BroadcastResponse)
async def read_broadcast(broadcast_id: UUID, db: DB, principal: Principal, group_id: UUID | None = None):
    return await detail(db, principal, broadcast_id, group_id)


@router.post("/{broadcast_id}/confirmations", response_model=BroadcastResponse, status_code=201)
async def confirm(broadcast_id: UUID, payload: ConfirmRequest, db: DB, principal: Principal):
    group_id = scoped_group(principal, payload.group_id)
    await BroadcastConfirmationService(db).confirm(broadcast_id, group_id, account_id=principal.account.id)
    return await detail(db, principal, broadcast_id, group_id)


@router.get("/{broadcast_id}/replies", response_model=list[ReplyResponse])
async def read_replies(broadcast_id: UUID, db: DB, principal: Principal, group_id: UUID | None = None):
    group_id = scoped_group(principal, group_id)
    record = await detail(db, principal, broadcast_id, group_id)
    return record.targets[0].replies


@router.post("/{broadcast_id}/replies", response_model=BroadcastResponse, status_code=201)
async def reply(broadcast_id: UUID, payload: ReplyRequest, db: DB, principal: Principal):
    group_id = scoped_group(principal, payload.group_id)
    await ReplyService(db).create(broadcast_id, group_id, account_id=principal.account.id,
        content=payload.content, ref_id=payload.ref_id)
    return await detail(db, principal, broadcast_id, group_id)
