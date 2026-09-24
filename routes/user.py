"""Authenticated account-profile routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_session
from database.service import AccountService, SessionService
from routes.auth import (
    AccountProfile,
    AuthenticatedPrincipal,
    account_profile,
    get_current_principal,
    hash_password,
    verify_password,
)


router = APIRouter(prefix="/users", tags=["users"])


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(..., max_length=255)


class PasswordUpdateRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


@router.get("/me", response_model=AccountProfile)
async def read_profile(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> AccountProfile:
    return account_profile(principal)


@router.patch("/me", response_model=AccountProfile)
async def update_profile(
    payload: ProfileUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    database_session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountProfile:
    updated_account = await AccountService(database_session).update(
        principal.account.id, display_name=payload.display_name
    )
    return AccountProfile(
        id=updated_account.id,
        account=updated_account.account,
        position_name=principal.position_name,
        group_name=principal.group_name,
        display_name=updated_account.display_name,
        is_active=updated_account.is_active,
        created_at=updated_account.created_at,
        updated_at=updated_account.updated_at,
    )


@router.patch("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def update_password(
    payload: PasswordUpdateRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    database_session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if not await verify_password(principal.account.password_hash, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await AccountService(database_session).update(
        principal.account.id, password_hash=await hash_password(payload.new_password)
    )
    await SessionService(database_session).revoke_all_for_account(principal.account.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
