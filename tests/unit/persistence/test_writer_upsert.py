"""Tests for the dialect-aware upsert in PersistenceWriter.

The SQLite variant runs in every test run. The PG variant is skipped unless
TRADER_TEST_DSN points at a postgresql+asyncpg DSN — opt-in to avoid coupling
the test suite to a live Postgres.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from contracts.schema import (
    AssetClass,
    Bar,
    Event,
    EventType,
    Instrument,
    Timeframe,
)
from persistence.engine import Database
from persistence.models import BarRow
from persistence.writer import PersistenceWriter


# A Bus stub: _handle doesn't touch the bus, so any object works.
class _NullBus:
    pass


def _bar_event(ts: datetime) -> Event:
    bar = Bar(
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        timeframe=Timeframe.D1,
        ts_open=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
    )
    return Event(
        type=EventType.BAR,
        source="test",
        payload=bar,
        ts_event=ts,
    )


async def test_upsert_idempotent_on_sqlite(tmp_db: Database) -> None:
    """Re-publishing the same BAR event produces exactly one row."""
    writer = PersistenceWriter(bus=_NullBus(), db=tmp_db)  # type: ignore[arg-type]
    ev = _bar_event(datetime(2026, 1, 1, tzinfo=timezone.utc))

    await writer._handle(ev)
    await writer._handle(ev)
    await writer._handle(ev)

    async with tmp_db.session() as s:
        rows = (await s.execute(select(BarRow))).scalars().all()

    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    assert rows[0].timeframe == "1d"
    # SQLite strips tzinfo on DateTime roundtrip; normalise to UTC for compare.
    assert rows[0].ts_open.replace(tzinfo=timezone.utc) == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )


PG_DSN = os.environ.get("TRADER_TEST_DSN", "")
needs_pg = pytest.mark.skipif(
    "postgresql" not in PG_DSN,
    reason="set TRADER_TEST_DSN to a postgresql+asyncpg DSN to enable",
)


@needs_pg
async def test_upsert_idempotent_on_postgres(tmp_path) -> None:
    """PG variant: ON CONFLICT DO NOTHING (vs SQLite's INSERT OR IGNORE)."""
    db = Database(PG_DSN)
    await db.create_all()
    try:
        writer = PersistenceWriter(bus=_NullBus(), db=db)  # type: ignore[arg-type]
        ev = _bar_event(datetime(2026, 1, 2, tzinfo=timezone.utc))

        await writer._handle(ev)
        await writer._handle(ev)

        async with db.session() as s:
            rows = (await s.execute(select(BarRow))).scalars().all()

        assert len(rows) == 1
    finally:
        await db.close()
