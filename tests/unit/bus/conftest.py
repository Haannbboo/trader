"""Shared fixtures for bus tests.

The Database is created against a file-backed SQLite in pytest's `tmp_path`
(not :memory:) so:
  - Each test gets a fresh, isolated database.
  - ON CONFLICT semantics match prod (the in-memory SQLite has some
    connection-visibility quirks under concurrent sessions).
  - Connection pool behavior is exercised as it will be in production.

Copied from tests/unit/persistence/conftest.py; in a future refactor, hoist
to a top-level conftest when a third test package needs it.
"""

from __future__ import annotations

import pytest_asyncio
from persistence.engine import Database


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    """A fresh Database bound to tmp_path/test.db, with schema created."""
    db_path = tmp_path / "test.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.create_all()
    try:
        yield db
    finally:
        await db.close()
