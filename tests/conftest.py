from __future__ import annotations

import os
import dotenv

import pytest
import pytest_asyncio

dotenv.load_dotenv()

_DATABASE_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
_MISSING_TEST_KEYS = [key for key in _DATABASE_KEYS if not os.getenv(f"TEST_{key}")]

POSTGRES_TEST_CONFIGURED = not _MISSING_TEST_KEYS
os.environ["POSTGRES_TEST_CONFIGURED"] = "1" if POSTGRES_TEST_CONFIGURED else "0"

if POSTGRES_TEST_CONFIGURED:
    for _key in _DATABASE_KEYS:
        os.environ[_key] = os.environ[f"TEST_{_key}"]
else:
    # The tests are marked skipped below, but imports still need a syntactically
    # valid URL so collection can finish without contacting PostgreSQL.
    os.environ.setdefault("DB_HOST", "localhost")
    os.environ.setdefault("DB_PORT", "5432")
    os.environ.setdefault("DB_NAME", "unconfigured_test_database")
    os.environ.setdefault("DB_USER", "unconfigured")
    os.environ.setdefault("DB_PASSWORD", "unconfigured")

from database.database import engine


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def dispose_async_engine_after_test() -> None:
    """Do not reuse asyncpg connections after pytest closes a test event loop."""
    yield
    await engine.dispose()
