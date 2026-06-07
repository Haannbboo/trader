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
def _utc(yyyy: int, mm: int, dd: int, hh: int = 0, mi: int = 0) -> datetime:
    return datetime(yyyy, mm, dd, hh, mi, tzinfo=timezone.utc)


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


# ---------------------------------------------------------------------------
# Multi-timeframe interleave — bars from different timeframes sort by
# ts_open + interval, not by ts_open alone.
# ---------------------------------------------------------------------------
async def test_replay_multi_timeframe_interleaves_by_open_plus_interval(
    tmp_db: Database,
) -> None:
    """An M5 bar that opens at 09:05 sorts AFTER an M1 bar that opens at
    09:02 (M1 closes at 09:03; M5 closes at 09:10) and BEFORE nothing in
    this fixture. The sort key is `ts_open + interval`, not `ts_open` alone —
    if it were `ts_open` alone, the M5 bar would come first (its ts_open is
    09:05, the latest in the input), but with the +interval key it sorts
    last (its ts_event is 09:10)."""
    bus = InProcessBus()
    history = Repository(tmp_db)

    bars = [
        _bar("AAPL", _utc(2026, 1, 1, 9, 0), timeframe=Timeframe.M1),  # closes 09:01
        _bar("AAPL", _utc(2026, 1, 1, 9, 5), timeframe=Timeframe.M5),  # closes 09:10
        _bar("AAPL", _utc(2026, 1, 1, 9, 1), timeframe=Timeframe.M1),  # closes 09:02
        _bar("AAPL", _utc(2026, 1, 1, 9, 2), timeframe=Timeframe.M1),  # closes 09:03
    ]
    await _write_bars(tmp_db, bars, source="test")

    sub = Subscription(
        event_types=(EventType.BAR,),
        instruments=(_instrument("AAPL"),),
    )
    events: List[Event] = []
    async for ev in bus.replay(
        sub, _utc(2026, 1, 1, 9, 0), _utc(2026, 1, 1, 10, 0), history=history
    ):
        events.append(ev)

    # Expected order by ts_event (ts_open + interval):
    #   09:00 M1 -> ts_event 09:01
    #   09:01 M1 -> ts_event 09:02
    #   09:02 M1 -> ts_event 09:03
    #   09:05 M5 -> ts_event 09:10
    expected = [
        (_utc(2026, 1, 1, 9, 0), Timeframe.M1),
        (_utc(2026, 1, 1, 9, 1), Timeframe.M1),
        (_utc(2026, 1, 1, 9, 2), Timeframe.M1),
        (_utc(2026, 1, 1, 9, 5), Timeframe.M5),
    ]
    actual = [(ev.payload.ts_open, ev.payload.timeframe) for ev in events]
    assert actual == expected


# ---------------------------------------------------------------------------
# Multi-instrument — bars from different symbols interleave in time order.
# ---------------------------------------------------------------------------
async def test_replay_multi_instrument_merges_in_time_order(
    tmp_db: Database,
) -> None:
    bus = InProcessBus()
    history = Repository(tmp_db)

    bars = [
        _bar("MSFT", _utc(2026, 1, 1), timeframe=Timeframe.D1),
        _bar("AAPL", _utc(2026, 1, 1), timeframe=Timeframe.D1),
        _bar("MSFT", _utc(2026, 1, 2), timeframe=Timeframe.D1),
        _bar("AAPL", _utc(2026, 1, 3), timeframe=Timeframe.D1),
    ]
    await _write_bars(tmp_db, bars, source="test")

    sub = Subscription(
        event_types=(EventType.BAR,),
        instruments=(_instrument("AAPL"), _instrument("MSFT")),
    )
    events: List[Event] = []
    async for ev in bus.replay(
        sub, _utc(2026, 1, 1), _utc(2026, 1, 4), history=history
    ):
        events.append(ev)

    assert [ev.payload.instrument.symbol for ev in events] == [
        "AAPL",
        "MSFT",
        "MSFT",
        "AAPL",
    ]
    assert [ev.payload.ts_open for ev in events] == [
        _utc(2026, 1, 1),
        _utc(2026, 1, 1),
        _utc(2026, 1, 2),
        _utc(2026, 1, 3),
    ]


# ---------------------------------------------------------------------------
# Synthetic Event envelope shape
# ---------------------------------------------------------------------------
async def test_replay_yields_synthetic_event_envelope(tmp_db: Database) -> None:
    bus = InProcessBus()
    history = Repository(tmp_db)

    bars = [
        _bar("AAPL", _utc(2026, 1, 1), timeframe=Timeframe.H1),
    ]
    await _write_bars(tmp_db, bars, source="test")

    sub = Subscription(
        event_types=(EventType.BAR,),
        instruments=(_instrument("AAPL"),),
    )
    before = datetime.now(timezone.utc)
    events: List[Event] = []
    async for ev in bus.replay(
        sub, _utc(2026, 1, 1), _utc(2026, 1, 2), history=history
    ):
        events.append(ev)
    after = datetime.now(timezone.utc)

    assert len(events) == 1
    ev = events[0]

    # payload is the original Bar
    assert ev.payload == bars[0]
    assert ev.type == EventType.BAR

    # ts_event = ts_open + interval (matches sort key)
    assert ev.ts_event == _utc(2026, 1, 1) + Timeframe.H1.interval

    # ts_ingest is between before and after (synthetic, ~now())
    assert before <= ev.ts_ingest <= after

    # source is the magic string for replay
    assert ev.source == "replay"

    # event_id is a fresh UUID (not the bar's, not from the DB)
    from uuid import UUID
    assert isinstance(ev.event_id, UUID)


async def test_replay_event_ids_are_unique_across_yields(tmp_db: Database) -> None:
    bus = InProcessBus()
    history = Repository(tmp_db)

    bars = [
        _bar("AAPL", _utc(2026, 1, 1), timeframe=Timeframe.D1),
        _bar("AAPL", _utc(2026, 1, 2), timeframe=Timeframe.D1),
        _bar("AAPL", _utc(2026, 1, 3), timeframe=Timeframe.D1),
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
    assert len({ev.event_id for ev in events}) == 3  # all distinct
