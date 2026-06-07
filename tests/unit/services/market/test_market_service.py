from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from contracts import (
    AssetClass,
    Bar,
    Event,
    EventType,
    Instrument,
    MarketChannel,
    Quote,
    Timeframe,
)
from contracts.ports import SourceCapabilities, SourceMode
from market import MarketService


class MockMarketSource:
    def __init__(self, name: str, asset_classes: tuple[AssetClass, ...]) -> None:
        self.name = name
        self._capabilities = SourceCapabilities(
            mode=SourceMode.PUSH,
            supports_streaming=True,
            asset_classes=asset_classes,
            historical=True,
        )
        self.started = False
        self.stopped = False
        self.last_quote_instrument: Instrument | None = None
        self.last_bars_args: tuple[Instrument, Timeframe, datetime, datetime] | None = (
            None
        )
        self.subscribed_instruments = None
        self.subscribed_channels = None
        self.events_to_yield: list[Event] = []
        self.bars_to_return: list[Bar] | None = None

    @property
    def capabilities(self) -> SourceCapabilities:
        return self._capabilities

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def health(self) -> bool:
        return True

    async def get_quote(self, instrument: Instrument) -> Quote:
        self.last_quote_instrument = instrument
        return Quote(
            instrument=instrument,
            bid=Decimal("100"),
            bid_size=Decimal("10"),
            ask=Decimal("101"),
            ask_size=Decimal("20"),
            ts_event=datetime.now(timezone.utc),
        )

    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        self.last_bars_args = (instrument, timeframe, start, end)
        if hasattr(self, "bars_to_return") and self.bars_to_return is not None:
            return self.bars_to_return
        return [
            Bar(
                instrument=instrument,
                timeframe=timeframe,
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("99"),
                close=Decimal("102"),
                volume=Decimal("1000"),
                ts_open=start,
            )
        ]

    async def subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]:
        self.subscribed_instruments = instruments
        self.subscribed_channels = channels
        # Helper to simulate streaming data
        for event in self.events_to_yield:
            yield event


class MockBus:
    def __init__(self) -> None:
        self.published_events: list[Event] = []
        self.subscribed_subscription = None
        self.events_to_yield: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published_events.append(event)

    async def subscribe(self, subscription) -> AsyncIterator[Event]:
        self.subscribed_subscription = subscription
        if self.events_to_yield:
            for event in self.events_to_yield:
                yield event
        else:
            yield Event(
                type=EventType.QUOTE,
                source="stock_source",
                payload=Quote(
                    instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
                    ts_event=datetime.now(timezone.utc),
                ),
                ts_event=datetime.now(timezone.utc),
            )
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_market_service_lifecycle() -> None:
    source = MockMarketSource("stock_source", (AssetClass.EQUITY,))
    bus = MockBus()
    service = MarketService(sources=[source], bus=bus)  # type: ignore[arg-type]

    await service.start()
    assert source.started

    await service.stop()
    assert source.stopped


def test_market_service_routing() -> None:
    stock_source = MockMarketSource("stock_source", (AssetClass.EQUITY,))
    option_source = MockMarketSource("option_source", (AssetClass.OPTION,))
    bus = MockBus()
    service = MarketService(
        sources=[stock_source, option_source],
        bus=bus,  # type: ignore[arg-type]
    )

    stock_instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    option_instrument = Instrument(
        symbol="AAPL260619C00150000", asset_class=AssetClass.OPTION
    )
    crypto_instrument = Instrument(symbol="BTCUSD", asset_class=AssetClass.CRYPTO)

    assert service._route(stock_instrument) is stock_source
    assert service._route(option_instrument) is option_source

    with pytest.raises(
        ValueError, match="No market source found that supports asset class"
    ):
        service._route(crypto_instrument)


@pytest.mark.asyncio
async def test_market_service_get_quote() -> None:
    stock_source = MockMarketSource("stock_source", (AssetClass.EQUITY,))
    bus = MockBus()
    service = MarketService(sources=[stock_source], bus=bus)  # type: ignore[arg-type]

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    quote = await service.get_quote(instrument)

    assert quote.instrument == instrument
    assert quote.bid == Decimal("100")
    assert stock_source.last_quote_instrument is instrument


@pytest.mark.asyncio
async def test_market_service_get_bars() -> None:
    stock_source = MockMarketSource("stock_source", (AssetClass.EQUITY,))
    bus = MockBus()
    service = MarketService(sources=[stock_source], bus=bus)  # type: ignore[arg-type]

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    start = datetime(2026, 6, 2, tzinfo=timezone.utc)
    end = datetime(2026, 6, 3, tzinfo=timezone.utc)
    timeframe = Timeframe.M1

    bars = await service.get_bars(instrument, timeframe, start, end)
    assert len(bars) == 1
    assert bars[0].close == Decimal("102")
    assert stock_source.last_bars_args == (instrument, timeframe, start, end)


@pytest.mark.asyncio
async def test_market_service_subscribe_flow() -> None:
    source = MockMarketSource("stock_source", (AssetClass.EQUITY,))
    bus = MockBus()
    service = MarketService(sources=[source], bus=bus)  # type: ignore[arg-type]

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    test_event = Event(
        type=EventType.QUOTE,
        source="stock_source",
        payload=Quote(instrument=instrument, ts_event=datetime.now(timezone.utc)),
        ts_event=datetime.now(timezone.utc),
    )

    # Configure mock data
    source.events_to_yield = [test_event]
    bus.events_to_yield = [test_event]

    # 1. Start a subscription
    iterator = service.subscribe([instrument], [MarketChannel.QUOTES])

    # Trigger generator execution
    events = []
    try:
        async for event in iterator:
            events.append(event)
            break
    finally:
        await iterator.aclose()

    assert len(events) == 1
    assert events[0] == test_event

    # Wait a brief moment to ensure tasks yield/cancel
    await asyncio.sleep(0.01)

    # 2. Check reference counts and pump tasks are cleaned up
    assert len(service._ref_counts) == 0
    assert len(service._pump_tasks) == 0


@pytest.mark.asyncio
async def test_market_service_multiplexing() -> None:
    source = MockMarketSource("stock_source", (AssetClass.EQUITY,))
    bus = MockBus()
    service = MarketService(sources=[source], bus=bus)  # type: ignore[arg-type]

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)

    # Call subscribe multiple times concurrently
    iter1 = service.subscribe([instrument], [MarketChannel.QUOTES])
    iter2 = service.subscribe([instrument], [MarketChannel.QUOTES])

    # Start first iteration
    gen1 = iter1.__aiter__()
    await gen1.__anext__()  # trigger first step to enter try block

    assert service._ref_counts[(instrument, MarketChannel.QUOTES)] == 1
    assert len(service._pump_tasks) == 1
    initial_task = service._pump_tasks[(instrument, MarketChannel.QUOTES)]

    # Start second iteration
    gen2 = iter2.__aiter__()
    await gen2.__anext__()

    # Ref count should increment but NO new task should be spun up
    assert service._ref_counts[(instrument, MarketChannel.QUOTES)] == 2
    assert service._pump_tasks[(instrument, MarketChannel.QUOTES)] is initial_task

    # Clean up gen1
    await gen1.aclose()
    assert service._ref_counts[(instrument, MarketChannel.QUOTES)] == 1
    assert len(service._pump_tasks) == 1

    # Clean up gen2
    await gen2.aclose()
    assert len(service._ref_counts) == 0
    assert len(service._pump_tasks) == 0


@pytest.mark.asyncio
async def test_market_service_read_through_cache(tmp_path) -> None:
    from persistence import Database, DbWriter, Repository

    # Setup fresh sqlite database for testing
    db_path = tmp_path / "test_market_service.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.create_all()

    source = MockMarketSource("stock_source", (AssetClass.EQUITY,))
    bus = MockBus()
    repo = Repository(db)
    writer = DbWriter(db)
    service = MarketService(
        sources=[source], bus=bus, repository=repo, writer=writer  # type: ignore[arg-type]
    )

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    timeframe = Timeframe.H1
    start = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 2, 14, 0, 0, tzinfo=timezone.utc)

    # 1. First query when cache is empty (Cache Miss)
    bar1 = Bar(
        instrument=instrument,
        timeframe=timeframe,
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1000"),
        ts_open=start,  # 12:00
    )
    bar2 = Bar(
        instrument=instrument,
        timeframe=timeframe,
        open=Decimal("101"),
        high=Decimal("106"),
        low=Decimal("100"),
        close=Decimal("102"),
        volume=Decimal("1200"),
        ts_open=datetime(2026, 6, 2, 13, 0, 0, tzinfo=timezone.utc),  # 13:00
    )

    source.bars_to_return = [bar1, bar2]

    bars = await service.get_bars(instrument, timeframe, start, end)
    assert len(bars) == 2
    assert bars[0].close == Decimal("101")
    assert bars[1].close == Decimal("102")

    # Verify bars are saved in the DB
    repo = Repository(db)
    db_bars = await repo.fetch_bars(instrument, timeframe, start, end)
    assert len(db_bars) == 2

    # 2. Second query with same range (Cache Hit)
    # Clear source return value so we'd fail if it hit the live source
    source.bars_to_return = None
    source.last_bars_args = None

    cached_bars = await service.get_bars(instrument, timeframe, start, end)
    assert len(cached_bars) == 2
    assert cached_bars[0].close == Decimal("101")
    # Verify the live source was NOT queried this time
    assert source.last_bars_args is None

    # 3. Third query with larger range (Partial cache miss / incomplete coverage)
    # Query from 11:00 to 14:00 (database only has 12:00 and 13:00 bars)
    larger_start = datetime(2026, 6, 2, 11, 0, 0, tzinfo=timezone.utc)
    bar0 = Bar(
        instrument=instrument,
        timeframe=timeframe,
        open=Decimal("99"),
        high=Decimal("101"),
        low=Decimal("98"),
        close=Decimal("100"),
        volume=Decimal("800"),
        ts_open=larger_start,  # 11:00
    )
    source.bars_to_return = [bar0, bar1, bar2]

    larger_bars = await service.get_bars(instrument, timeframe, larger_start, end)
    assert len(larger_bars) == 3
    assert larger_bars[0].close == Decimal("100")
    # Verify the live source WAS queried
    assert source.last_bars_args is not None

    # Clean up DB connection
    await db.close()


def test_timeframe_duration_validation() -> None:
    from market import _timeframe_duration

    # Valid units
    assert _timeframe_duration(Timeframe.M1) == 60.0
    assert _timeframe_duration(Timeframe.H1) == 3600.0
    assert _timeframe_duration(Timeframe.D1) == 86400.0

    # Invalid unit test
    class CustomTimeframe:
        value = "5x"

    with pytest.raises(ValueError, match="Unsupported timeframe unit"):
        _timeframe_duration(CustomTimeframe)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_market_service_subscribe_trades_event_types() -> None:
    source = MockMarketSource("stock_source", (AssetClass.EQUITY,))
    bus = MockBus()
    service = MarketService(sources=[source], bus=bus)  # type: ignore[arg-type]

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)

    # Subscribe to TRADES channel
    iterator = service.subscribe([instrument], [MarketChannel.TRADES])
    try:
        async for _ in iterator:
            break
    finally:
        await iterator.aclose()

    assert bus.subscribed_subscription is not None
    assert EventType.QUOTE in bus.subscribed_subscription.event_types
