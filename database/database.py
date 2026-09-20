"""SQLAlchemy asynchronous PostgreSQL configuration."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class DatabaseConfigurationError(RuntimeError):
    """Raised when the PostgreSQL connection environment is incomplete."""


def build_database_url() -> URL:
    """Build the asyncpg URL from the required database environment variables."""
    required_keys = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required_keys if not os.getenv(key)]
    if missing:
        joined = ", ".join(missing)
        raise DatabaseConfigurationError(
            f"Missing required PostgreSQL environment variable(s): {joined}"
        )

    try:
        port = int(os.environ["DB_PORT"])
    except ValueError as error:
        raise DatabaseConfigurationError("DB_PORT must be an integer") from error

    return URL.create(
        "postgresql+asyncpg",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=port,
        database=os.environ["DB_NAME"],
    )


DATABASE_URL = build_database_url()

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield one FastAPI-compatible database session per request."""
    async with AsyncSessionLocal() as session:
        yield session
