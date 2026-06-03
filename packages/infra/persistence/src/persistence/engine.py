"""
persistence.engine — async engine + session management. Connection string
comes from config (secret in .env); nothing here reads os.environ directly,
same rule as adapters. For Timescale/Postgres use the asyncpg driver
("postgresql+asyncpg://..."); SQLite+aiosqlite is fine for tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from persistence.models import Base


class Database:
    def __init__(self, dsn: str) -> None:
        """dsn injected by config, e.g. postgresql+asyncpg://user:pw@localhost/ta."""
        self._engine: AsyncEngine = create_async_engine(dsn, pool_pre_ping=True)
        self._sessionmaker = sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @property
    def dialect_name(self) -> str:
        """The SQLAlchemy dialect name (e.g. "postgresql", "sqlite").

        Exposed so callers (the writer's upsert helper) can branch on dialect
        without reaching into AsyncSession.bind. Kept as a simple property —
        the engine caches its dialect after construction, so this is O(1).
        """
        return self._engine.dialect.name

    async def create_all(self) -> None:
        """Create tables (dev/test). In prod use migrations (alembic) + the
        one-time create_hypertable() calls for Timescale."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Scoped session; commit on success, rollback on error."""
        async with self._sessionmaker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def close(self) -> None:
        await self._engine.dispose()
