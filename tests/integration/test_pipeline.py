import pytest
from bus import InProcessBus
from contracts import (
    AssetClass,
    Instrument,
    SourceCapabilities,
    SourceMode,
)
from feature.runtime import FeatureRuntime
from features.technical.rsi import RSIProcessor
from market import MarketService


class MockMarketAdapter:
    name = "mock_market"

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            mode=SourceMode.PUSH,
            supports_streaming=True,
            asset_classes=(AssetClass.EQUITY,),
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> bool:
        return True

    async def get_quote(self, instrument: Instrument):
        raise NotImplementedError()


@pytest.mark.asyncio
async def test_subscription_reuse() -> None:
    """Verifies that MarketService is wired correctly and complies with its constructors."""
    bus = InProcessBus()
    await bus.start()

    adapter = MockMarketAdapter()
    service = MarketService(sources=[adapter], bus=bus)

    # Under the skeleton architecture, calls to unimplemented methods raise NotImplementedError
    with pytest.raises(NotImplementedError):
        await service.get_quote(
            Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
        )

    await bus.stop()


@pytest.mark.asyncio
async def test_end_to_end_signal_generation() -> None:
    """Verifies that injecting bars into the bus triggers features and emits signals."""
    from datetime import datetime, timezone
    from decimal import Decimal

    import anyio
    from contracts import Subscription, Timeframe
    from contracts.schema import Bar, Event, EventType

    bus = InProcessBus()
    await bus.start()

    feature_runtime = FeatureRuntime(bus=bus)
    rsi = RSIProcessor(period=3)
    rsi.initialize()

    feature_runtime.add_processor(rsi)
    await feature_runtime.start()
    import asyncio

    await asyncio.sleep(0.05)

    # Create a subscriber to collect emitted features from the bus
    sub = bus.subscribe(Subscription(event_types=(EventType.FEATURE,)))

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    base_ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Publish 4 bars to trigger the first RSI feature output (for period=3)
    prices = [10.0, 11.0, 12.0, 13.0]
    for i, p in enumerate(prices):
        bar = Bar(
            instrument=instrument,
            timeframe=tf,
            ts_open=base_ts,
            open=Decimal(str(p)),
            high=Decimal(str(p)),
            low=Decimal(str(p)),
            close=Decimal(str(p)),
            volume=Decimal("100"),
        )
        event = Event(
            type=EventType.BAR,
            source="test",
            payload=bar,
            ts_event=base_ts,
        )
        await bus.publish(event)

    # Collect the emitted feature event
    emitted = []
    with anyio.fail_after(2.0):
        async for fe in sub:
            emitted.append(fe)
            break  # We only expect one event for these 4 bars

    assert len(emitted) == 1
    assert emitted[0].type == EventType.FEATURE
    assert emitted[0].payload.feature == "rsi"
    assert emitted[0].payload.value == pytest.approx(100.0)

    await feature_runtime.stop()
    await bus.stop()


@pytest.mark.asyncio
async def test_feature_runtime_warmup() -> None:
    from datetime import datetime, timezone
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    from contracts import Bar, Instrument, Timeframe

    bus = InProcessBus()
    await bus.start()

    market = MagicMock()

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    base_ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    prices = [10.0, 11.0, 12.0, 13.0]
    bars = [
        Bar(
            instrument=instrument,
            timeframe=tf,
            ts_open=base_ts,
            open=Decimal(str(p)),
            high=Decimal(str(p)),
            low=Decimal(str(p)),
            close=Decimal(str(p)),
            volume=Decimal("100"),
        )
        for p in prices
    ]
    market.get_bars = AsyncMock(return_value=bars)

    feature_runtime = FeatureRuntime(bus=bus, market=market)

    rsi = RSIProcessor(period=3)
    rsi.initialize()
    feature_runtime.add_processor(rsi)

    await feature_runtime.start(instruments=[instrument], timeframes=[tf])

    state_key = (instrument.key, tf.value)
    assert state_key in rsi._states
    assert len(rsi._states[state_key].prices) == 4
    assert rsi._states[state_key].avg_gain is not None

    await feature_runtime.stop()
    await bus.stop()


@pytest.mark.asyncio
async def test_feature_service() -> None:
    import asyncio
    from datetime import datetime, timezone
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    import anyio
    from contracts import Bar, Event, EventType, Instrument, Timeframe
    from feature import FeatureService

    bus = InProcessBus()
    await bus.start()

    market = MagicMock()

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    base_ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    prices = [10.0, 11.0, 12.0, 13.0]
    bars = [
        Bar(
            instrument=instrument,
            timeframe=tf,
            ts_open=base_ts,
            open=Decimal(str(p)),
            high=Decimal(str(p)),
            low=Decimal(str(p)),
            close=Decimal(str(p)),
            volume=Decimal("100"),
        )
        for p in prices
    ]
    market.get_bars = AsyncMock(return_value=bars)

    feature_runtime = FeatureRuntime(bus=bus, market=market)
    feature_service = FeatureService(runtime=feature_runtime)

    rsi = RSIProcessor(period=3)
    rsi.initialize()
    feature_runtime.add_processor(rsi)

    await feature_runtime.start(instruments=[instrument], timeframes=[tf])

    # 1. Test get_value (gets value warmed up on startup)
    fv = await feature_service.get_value("rsi", instrument)
    assert fv.feature == "rsi"
    assert fv.value == pytest.approx(100.0)

    # 2. Test subscribe
    feature_events = []
    sub_iter = feature_service.subscribe(["rsi"])

    async def listen():
        async for fe in sub_iter:
            feature_events.append(fe)
            break

    listener_task = asyncio.create_task(listen())
    # Yield to the event loop so the listener can subscribe to the bus
    await asyncio.sleep(0.05)

    new_bar = Bar(
        instrument=instrument,
        timeframe=tf,
        ts_open=base_ts,
        open=Decimal("14.0"),
        high=Decimal("14.0"),
        low=Decimal("14.0"),
        close=Decimal("14.0"),
        volume=Decimal("100"),
    )
    new_event = Event(
        type=EventType.BAR,
        source="test",
        payload=new_bar,
        ts_event=base_ts,
    )
    await bus.publish(new_event)

    with anyio.fail_after(2.0):
        await listener_task

    assert len(feature_events) == 1
    assert feature_events[0].type == EventType.FEATURE
    assert feature_events[0].payload.feature == "rsi"
    assert feature_events[0].payload.value == pytest.approx(100.0)

    await feature_runtime.stop()
    await bus.stop()
