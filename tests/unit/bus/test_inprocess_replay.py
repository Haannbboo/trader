"""Tests for InProcessBus.replay().

The bus is persistence-agnostic — `replay()` takes a `HistoryStore` per call.
This file drives replay end-to-end against a real `Repository` bound to a
file-backed SQLite database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import List

import pytest
from bus import InProcessBus
from contracts import (
    AssetClass,
    Bar,
    Event,
    EventType,
    Instrument,
    Subscription,
    Timeframe,
)
from persistence import Repository
from persistence.engine import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utc(yyyy: int, mm: int, dd: int, hh: int = 0) -> datetime:
    return datetime(yyyy, mm, dd, hh, tzinfo=timezone.utc)


def _instrument(symbol: str = "AAPL") -> Instrument:
    return Instrument(symbol=symbol, asset_class=AssetClass.EQUITY)


def _bar(
    symbol: str,
    ts_open: datetime,
    *,
    timeframe: Timeframe = Timeframe.D1,
) -> Bar:
    return Bar(
        instrument=_instrument(symbol),
        timeframe=timeframe,
        ts_open=ts_open,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
    )


async def _write_bars(db: Database, bars: List[Bar], source: str = "test") -> None:
    """Persist bars via DbWriter so the test exercises the same write path
    the live system uses."""
    from persistence import DbWriter

    w = DbWriter(db)
    await w.store_bars(bars, source=source)


# ---------------------------------------------------------------------------
# Guard: empty subscription.instruments
# ---------------------------------------------------------------------------
async def test_replay_empty_instruments_raises_value_error(tmp_db: Database) -> None:
    bus = InProcessBus()
    sub = Subscription(event_types=(EventType.BAR,), instruments=())
    history = Repository(tmp_db)

    with pytest.raises(ValueError, match="subscription.instruments"):
        async for _ in bus.replay(
            sub, _utc(2026, 1, 1), _utc(2026, 1, 2), history=history
        ):
            pass


# ---------------------------------------------------------------------------
# Basic read: one instrument, one timeframe, sorted by ts_open + interval
# ---------------------------------------------------------------------------
async def test_replay_single_instrument_single_timeframe_sorted(
    tmp_db: Database,
) -> None:
    """Bars come back in `ts_open + interval` order, even when the DB has them
    in a different order (the Repository.fetch_bars() already orders by ts_open
    ASC, so this test pins down the sort key across timeframes; here we just
    verify the basic happy path."""
    bus = InProcessBus()
    history = Repository(tmp_db)

    # Insert out of order on purpose.
    bars = [
        _bar("AAPL", _utc(2026, 1, 3), timeframe=Timeframe.D1),
        _bar("AAPL", _utc(2026, 1, 1), timeframe=Timeframe.D1),
        _bar("AAPL", _utc(2026, 1, 2), timeframe=Timeframe.D1),
    ]
    await _write_bars(tmp_db, bars, source="test")

    sub = Subscription(
        event_types=(EventType.BAR,),
        instruments=(_instrument("AAPL"),),
    )
    events: List[Event] = []
    async for ev in bus.replay(
        sub, _utc(2026, 1, 1), _utc(2026, 1, 4), history=history
    ):
        events.append(ev)

    assert len(events) == 3
    assert [ev.payload.ts_open for ev in events] == [
        _utc(2026, 1, 1),
        _utc(2026, 1, 2),
        _utc(2026, 1, 3),
    ]
    assert all(ev.type == EventType.BAR for ev in events)
