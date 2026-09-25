"""Bearer-token authentication routes and reusable current-user dependencies."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_session
from database.model import Account, Session
from database.service import (
    AccountService,
    GroupService,
    NotFoundError,
    PositionService,
    SessionService,
)


router = APIRouter(prefix="/auth", tags=["auth"])

_bearer_scheme = HTTPBearer(auto_error=False)
_password_hasher = PasswordHasher()


def _duration_from_hours(variable_name: str, default_hours: int) -> timedelta:
    raw_value = os.getenv(variable_name, str(default_hours))
    try:
        hours = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{variable_name} must be a positive integer") from error
    if hours <= 0:
        raise RuntimeError(f"{variable_name} must be a positive integer")
    return timedelta(hours=hours)


def _session_ttl() -> timedelta:
    return _duration_from_hours("AUTH_SESSION_TTL_HOURS", 24)


def _session_force_ttl() -> timedelta:
    return _duration_from_hours("AUTH_SESSION_FORCE_TTL_HOURS", 24 * 30)


def _session_renewal_window() -> timedelta:
    return _duration_from_hours("AUTH_SESSION_RENEWAL_WINDOW_HOURS", 12)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    """Return the database-safe SHA-256 representation of an opaque token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def hash_password(password: str) -> str:
    """Hash a password without blocking FastAPI's event loop."""
    return await asyncio.to_thread(_password_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    """Safely verify an Argon2 password hash without exposing hash errors."""
    try:
        return await asyncio.to_thread(_password_hasher.verify, password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


async def password_needs_rehash(password_hash: str) -> bool:
    try:
        return await asyncio.to_thread(_password_hasher.check_needs_rehash, password_hash)
    except InvalidHashError:
        return False


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


class LoginRequest(BaseModel):
    account: str = Field(min_length=1, max_length=64)
    position_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class AccountProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account: str
    position_name: str
    group_name: str
    display_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    account: Account
    session: Session
    position_name: str
    group_name: str


def account_profile(principal: AuthenticatedPrincipal) -> AccountProfile:
    """Create the safe API representation of an authenticated account."""
    return AccountProfile(
        id=principal.account.id,
        account=principal.account.account,
        position_name=principal.position_name,
        group_name=principal.group_name,
        display_name=principal.account.display_name,
        is_active=principal.account.is_active,
        created_at=principal.account.created_at,
        updated_at=principal.account.updated_at,
    )


async def get_current_principal(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
    database_session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthenticatedPrincipal:
    """Resolve an active Bearer token to its enabled account and persisted session."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    sessions = SessionService(database_session)
    try:
        current_session = await sessions.get_active_by_token_hash(
            hash_token(credentials.credentials)
        )
        account = await AccountService(database_session).get(current_session.account_id)
        position = await PositionService(database_session).get(account.position_id)
        group = await GroupService(database_session).get(account.group_id)
    except NotFoundError as error:
        raise _unauthorized() from error

    if not account.is_active:
        raise _unauthorized()

    try:
        await sessions.touch(
            current_session.id,
            renewal_ttl=_session_ttl(),
            renewal_window=_session_renewal_window(),
        )
    except NotFoundError as error:
        raise _unauthorized() from error
    return AuthenticatedPrincipal(
        account=account,
        session=current_session,
        position_name=position.name,
        group_name=group.name,
    )


async def get_current_account(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> Account:
    """Dependency used by routes that only need the authenticated account."""
    return principal.account


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest,
    database_session: Annotated[AsyncSession, Depends(get_session)],
) -> AccessTokenResponse:
    positions = PositionService(database_session)
    accounts = AccountService(database_session)
    try:
        position = await positions.get_by_name(payload.position_name)
        account = await accounts.get_by_account_position(payload.account, position.id)
    except NotFoundError as error:
        raise _unauthorized() from error

    if not account.is_active or not await verify_password(account.password_hash, payload.password):
        raise _unauthorized()

    if await password_needs_rehash(account.password_hash):
        await accounts.update(
            account.id, password_hash=await hash_password(payload.password)
        )

    token = secrets.token_urlsafe(32)
    now = _utc_now()
    session_ttl = _session_ttl()
    force_ttl = _session_force_ttl()
    if force_ttl < session_ttl:
        raise RuntimeError(
            "AUTH_SESSION_FORCE_TTL_HOURS must be at least AUTH_SESSION_TTL_HOURS"
        )
    expires_at = now + session_ttl
    await SessionService(database_session).create(
        account_id=account.id,
        token_hash=hash_token(token),
        expires_at=expires_at,
        force_ttl_hours=int(force_ttl.total_seconds() // 3600),
    )
    return AccessTokenResponse(access_token=token, expires_at=expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    database_session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    await SessionService(database_session).revoke(principal.session.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AccountProfile)
async def current_identity(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> AccountProfile:
    return account_profile(principal)
