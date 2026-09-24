from __future__ import annotations

import pytest

from routes.auth import hash_password, hash_token, verify_password


@pytest.mark.asyncio
async def test_password_hashes_verify_without_exposing_plaintext() -> None:
    password_hash = await hash_password("secret")

    assert password_hash != "secret"
    assert await verify_password(password_hash, "secret")
    assert not await verify_password(password_hash, "incorrect")


def test_token_hash_is_deterministic_and_not_the_raw_token() -> None:
    token = "opaque-token"

    assert hash_token(token) == hash_token(token)
    assert hash_token(token) != token
