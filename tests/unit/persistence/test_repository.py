"""Tests for Repository (the read face of the persistence layer)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from contracts.schema import (
    AssetClass,
    Bar,
    Event,
    EventType,
    Instrument,
    NewsItem,
    OptionRight,
    Timeframe,
)
from persistence.engine import Database
from persistence.repository import Repository

# --- helpers ---------------------------------------------------------------


def _utc(yyyy: int, mm: int, dd: int) -> datetime:
    return datetime(yyyy, mm, dd, tzinfo=timezone.utc)


def _bar_event(symbol: str, ts: datetime, source: str = "test") -> Event:
    bar = Bar(
        instrument=Instrument(symbol=symbol, asset_class=AssetClass.EQUITY),
        timeframe=Timeframe.D1,
        ts_open=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
    )
    return Event(type=EventType.BAR, source=source, payload=bar, ts_event=ts)


def _option_bar_event(ts: datetime) -> Event:
    inst = Instrument(
        symbol="SPX",
        asset_class=AssetClass.OPTION,
        expiry=_utc(2026, 6, 1),
        strike=Decimal("5000"),
        right=OptionRight.PUT,
    )
    bar = Bar(
        instrument=inst,
        timeframe=Timeframe.D1,
        ts_open=ts,
        open=Decimal("10"),
        high=Decimal("12"),
        low=Decimal("9"),
        close=Decimal("11"),
        volume=Decimal("100"),
    )
    return Event(type=EventType.BAR, source="test", payload=bar, ts_event=ts)


class _NullBus:
    pass


async def _write_bars(db: Database, events: list[Event]) -> None:
    from persistence.writer import PersistenceWriter

    w = PersistenceWriter(bus=_NullBus(), db=db)  # type: ignore[arg-type]
    for ev in events:
        await w._handle(ev)


# --- fetch_bars ------------------------------------------------------------


async def test_fetch_bars_empty_returns_empty_list(tmp_db: Database) -> None:
    repo = Repository(tmp_db)
    bars = await repo.fetch_bars(
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        timeframe=Timeframe.D1,
        start=_utc(2026, 1, 1),
        end=_utc(2026, 1, 31),
    )
    assert bars == []


async def test_fetch_bars_returns_all_in_range_ordered(tmp_db: Database) -> None:
    ts1, ts2, ts3 = _utc(2026, 1, 1), _utc(2026, 1, 2), _utc(2026, 1, 3)
    await _write_bars(
        tmp_db,
        [
            _bar_event("AAPL", ts1),
            _bar_event("AAPL", ts2),
            _bar_event("AAPL", ts3),
        ],
    )
    repo = Repository(tmp_db)
    bars = await repo.fetch_bars(
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        timeframe=Timeframe.D1,
        start=_utc(2026, 1, 1),
        end=_utc(2026, 1, 31),
    )
    assert [b.ts_open for b in bars] == [ts1, ts2, ts3]


async def test_fetch_bars_filters_by_time_range(tmp_db: Database) -> None:
    ts1, ts2, ts3 = _utc(2026, 1, 1), _utc(2026, 1, 15), _utc(2026, 1, 31)
    await _write_bars(
        tmp_db,
        [
            _bar_event("AAPL", ts1),
            _bar_event("AAPL", ts2),
            _bar_event("AAPL", ts3),
        ],
    )
    repo = Repository(tmp_db)
    bars = await repo.fetch_bars(
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        timeframe=Timeframe.D1,
        start=_utc(2026, 1, 10),
        end=_utc(2026, 1, 20),
    )
    assert [b.ts_open for b in bars] == [ts2]


async def test_fetch_bars_filters_by_timeframe(tmp_db: Database) -> None:
    """Bars on a different timeframe must NOT be returned."""
    ts = _utc(2026, 1, 1)
    h1_bar = Bar(
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        timeframe=Timeframe.H1,
        ts_open=ts,
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
    )
    await _write_bars(
        tmp_db,
        [
            _bar_event("AAPL", ts),  # D1
            Event(type=EventType.BAR, source="test", payload=h1_bar, ts_event=ts),
        ],
    )
    repo = Repository(tmp_db)
    bars = await repo.fetch_bars(
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        timeframe=Timeframe.D1,
        start=_utc(2026, 1, 1),
        end=_utc(2026, 1, 2),
    )
    assert len(bars) == 1
    assert bars[0].timeframe == Timeframe.D1


async def test_fetch_bars_round_trips_option_instrument(tmp_db: Database) -> None:
    """An option bar's flattened instrument columns must re-inflate losslessly."""
    ts = _utc(2026, 1, 1)
    await _write_bars(tmp_db, [_option_bar_event(ts)])
    repo = Repository(tmp_db)
    bars = await repo.fetch_bars(
        instrument=Instrument(
            symbol="SPX",
            asset_class=AssetClass.OPTION,
            expiry=_utc(2026, 6, 1),
            strike=Decimal("5000"),
            right=OptionRight.PUT,
        ),
        timeframe=Timeframe.D1,
        start=_utc(2026, 1, 1),
        end=_utc(2026, 1, 2),
    )
    assert len(bars) == 1
    inst = bars[0].instrument
    assert inst.symbol == "SPX"
    assert inst.asset_class == AssetClass.OPTION
    assert inst.expiry == _utc(2026, 6, 1)
    assert inst.strike == Decimal("5000")
    assert inst.right == OptionRight.PUT
    assert inst.multiplier == Decimal(1)


async def test_fetch_bars_start_after_end_returns_empty(tmp_db: Database) -> None:
    """start > end is a contract issue, not an error. Logged, returns []."""
    await _write_bars(tmp_db, [_bar_event("AAPL", _utc(2026, 1, 1))])
    repo = Repository(tmp_db)
    bars = await repo.fetch_bars(
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        timeframe=Timeframe.D1,
        start=_utc(2026, 1, 31),
        end=_utc(2026, 1, 1),
    )
    assert bars == []


# --- fetch_news ------------------------------------------------------------


def _news_event(news_id: str, ts: datetime, source: str = "rss") -> Event:
    n = NewsItem(
        id=news_id,
        source=source,
        headline="h",
        published_at=ts,
    )
    return Event(type=EventType.NEWS, source=source, payload=n, ts_event=ts)


async def _write_news(db: Database, events: list[Event]) -> None:
    from persistence.writer import PersistenceWriter

    w = PersistenceWriter(bus=_NullBus(), db=db)  # type: ignore[arg-type]
    for ev in events:
        await w._handle(ev)


async def test_fetch_news_empty_returns_empty_list(tmp_db: Database) -> None:
    repo = Repository(tmp_db)
    assert await repo.fetch_news() == []


async def test_fetch_news_returns_all_in_range_ordered(tmp_db: Database) -> None:
    ts1, ts2, ts3 = _utc(2026, 1, 1), _utc(2026, 1, 2), _utc(2026, 1, 3)
    await _write_news(
        tmp_db,
        [
            _news_event("a", ts1),
            _news_event("b", ts2),
            _news_event("c", ts3),
        ],
    )
    repo = Repository(tmp_db)
    items = await repo.fetch_news(
        start=_utc(2026, 1, 1),
        end=_utc(2026, 1, 31),
    )
    assert [n.id for n in items] == ["a", "b", "c"]
    assert [n.published_at for n in items] == [ts1, ts2, ts3]


async def test_fetch_news_filters_by_time_range(tmp_db: Database) -> None:
    ts1, ts2, ts3 = _utc(2026, 1, 1), _utc(2026, 1, 15), _utc(2026, 1, 31)
    await _write_news(
        tmp_db,
        [
            _news_event("a", ts1),
            _news_event("b", ts2),
            _news_event("c", ts3),
        ],
    )
    repo = Repository(tmp_db)
    items = await repo.fetch_news(
        start=_utc(2026, 1, 10),
        end=_utc(2026, 1, 20),
    )
    assert [n.id for n in items] == ["b"]


async def test_fetch_news_round_trips_fields(tmp_db: Database) -> None:
    ts = _utc(2026, 1, 1)
    await _write_news(
        tmp_db,
        [
            Event(
                type=EventType.NEWS,
                source="rss",
                payload=NewsItem(
                    id="n1",
                    source="rss",
                    published_at=ts,
                    headline="Hello",
                    body="World",
                    url="https://example.com",
                ),
                ts_event=ts,
            )
        ],
    )
    repo = Repository(tmp_db)
    items = await repo.fetch_news()
    assert len(items) == 1
    n = items[0]
    assert n.id == "n1"
    assert n.source == "rss"
    assert n.headline == "Hello"
    assert n.body == "World"
    assert n.url == "https://example.com"


async def test_fetch_news_start_after_end_returns_empty(tmp_db: Database) -> None:
    await _write_news(tmp_db, [_news_event("a", _utc(2026, 1, 1))])
    repo = Repository(tmp_db)
    assert await repo.fetch_news(start=_utc(2026, 1, 31), end=_utc(2026, 1, 1)) == []


# --- fetch_fills ------------------------------------------------------------


def _fill_event(fill_id: str, ts: datetime, broker_order_id: str = "bo-1") -> Event:
    from contracts.schema import Fill, Side

    f = Fill(
        fill_id=fill_id, broker_order_id=broker_order_id,
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY, quantity=Decimal("10"), price=Decimal("150.5"),
        ts_event=ts, fee=Decimal("1"),
    )
    return Event(type=EventType.FILL, source="test", payload=f, ts_event=ts)


async def _write_fills(db: Database, events: list[Event]) -> None:
    from persistence.writer import PersistenceWriter

    w = PersistenceWriter(bus=_NullBus(), db=db)  # type: ignore[arg-type]
    for ev in events:
        await w._handle(ev)


async def test_fetch_fills_empty_returns_empty_list(tmp_db: Database) -> None:
    repo = Repository(tmp_db)
    assert await repo.fetch_fills() == []


async def test_fetch_fills_returns_all_in_range_ordered(tmp_db: Database) -> None:
    ts1, ts2, ts3 = _utc(2026, 1, 1), _utc(2026, 1, 2), _utc(2026, 1, 3)
    await _write_fills(tmp_db, [
        _fill_event("f1", ts1),
        _fill_event("f2", ts2),
        _fill_event("f3", ts3),
    ])
    repo = Repository(tmp_db)
    fills = await repo.fetch_fills(
        start=_utc(2026, 1, 1), end=_utc(2026, 1, 31),
    )
    assert [f.fill_id for f in fills] == ["f1", "f2", "f3"]


async def test_fetch_fills_filters_by_broker_order_id(tmp_db: Database) -> None:
    ts = _utc(2026, 1, 1)
    await _write_fills(tmp_db, [
        _fill_event("f1", ts, broker_order_id="bo-1"),
        _fill_event("f2", ts, broker_order_id="bo-2"),
        _fill_event("f3", ts, broker_order_id="bo-1"),
    ])
    repo = Repository(tmp_db)
    fills = await repo.fetch_fills(broker_order_id="bo-1")
    assert {f.fill_id for f in fills} == {"f1", "f3"}


async def test_fetch_fills_round_trips_option_instrument(tmp_db: Database) -> None:
    """An option fill's flattened columns must re-inflate losslessly."""
    from contracts.schema import Fill, Side

    ts = _utc(2026, 1, 1)
    inst = Instrument(
        symbol="SPX", asset_class=AssetClass.OPTION,
        expiry=_utc(2026, 6, 1), strike=Decimal("5000"),
        right=OptionRight.CALL, multiplier=Decimal(100),
    )
    f = Fill(
        fill_id="f-opt", broker_order_id="bo-1",
        instrument=inst, side=Side.SELL,
        quantity=Decimal("2"), price=Decimal("15.5"),
        ts_event=ts, fee=Decimal("0.5"),
    )
    await _write_fills(tmp_db, [
        Event(type=EventType.FILL, source="test", payload=f, ts_event=ts)
    ])
    repo = Repository(tmp_db)
    fills = await repo.fetch_fills()
    assert len(fills) == 1
    out_inst = fills[0].instrument
    assert out_inst.symbol == "SPX"
    assert out_inst.asset_class == AssetClass.OPTION
    assert out_inst.expiry == _utc(2026, 6, 1)
    assert out_inst.strike == Decimal("5000")
    assert out_inst.right == OptionRight.CALL
    assert out_inst.multiplier == Decimal(100)


async def test_fetch_fills_start_after_end_returns_empty(tmp_db: Database) -> None:
    await _write_fills(tmp_db, [_fill_event("f1", _utc(2026, 1, 1))])
    repo = Repository(tmp_db)
    assert await repo.fetch_fills(start=_utc(2026, 1, 31), end=_utc(2026, 1, 1)) == []
