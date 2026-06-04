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
from sqlalchemy import delete, select


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
    not PG_DSN.startswith("postgresql+asyncpg://"),
    reason="set TRADER_TEST_DSN to a postgresql+asyncpg DSN to enable",
)


@needs_pg
async def test_upsert_idempotent_on_postgres() -> None:
    """PG variant: ON CONFLICT DO NOTHING (vs SQLite's INSERT OR IGNORE).

    Cleans up the row it wrote so a re-run against the same DSN starts clean.
    """
    db = Database(PG_DSN)
    await db.create_all()
    target_ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    try:
        writer = PersistenceWriter(bus=_NullBus(), db=db)  # type: ignore[arg-type]
        ev = _bar_event(target_ts)

        await writer._handle(ev)
        await writer._handle(ev)

        async with db.session() as s:
            rows = (
                (
                    await s.execute(
                        select(BarRow).where(
                            BarRow.instrument_key == "AAPL|equity",
                            BarRow.timeframe == "1d",
                            BarRow.ts_open == target_ts,
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert len(rows) == 1
    finally:
        async with db.session() as s:
            await s.execute(
                delete(BarRow).where(
                    BarRow.instrument_key == "AAPL|equity",
                    BarRow.timeframe == "1d",
                    BarRow.ts_open == target_ts,
                )
            )
        await db.close()


async def test_upsert_does_not_swallow_non_duplicates_on_sqlite(
    tmp_db: Database,
) -> None:
    """Upsert must dedupe identical PKs but still persist DISTINCT events.

    Guards against an over-eager dedupe (e.g. WHERE NOT EXISTS on the wrong
    column) that would silently swallow legitimate new bars.
    """
    writer = PersistenceWriter(bus=_NullBus(), db=tmp_db)  # type: ignore[arg-type]
    ts_a = datetime(2026, 2, 1, tzinfo=timezone.utc)
    ts_b = datetime(2026, 2, 2, tzinfo=timezone.utc)

    await writer._handle(_bar_event(ts_a))
    await writer._handle(_bar_event(ts_b))
    # Re-publish one of them — must not change the row count.
    await writer._handle(_bar_event(ts_a))

    async with tmp_db.session() as s:
        rows = (await s.execute(select(BarRow))).scalars().all()

    assert len(rows) == 2
    ts_set = {r.ts_open.replace(tzinfo=timezone.utc) for r in rows}
    assert ts_set == {ts_a, ts_b}
