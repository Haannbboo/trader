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
from features.technical.rsi import RSIProcessor


def _make_bar_event(
    instrument: Instrument,
    timeframe: Timeframe,
    close: float,
    ts: datetime,
) -> Event[Bar]:
    bar = Bar(
        instrument=instrument,
        timeframe=timeframe,
        ts_open=ts,
        open=Decimal(str(close)),
        high=Decimal(str(close)),
        low=Decimal(str(close)),
        close=Decimal(str(close)),
        volume=Decimal("100"),
    )
    return Event(
        type=EventType.BAR,
        source="test",
        payload=bar,
        ts_event=ts,
    )


@pytest.mark.asyncio
async def test_rsi_calculation_wilder() -> None:
    """Verifies that RSIProcessor computes mathematically correct Welles Wilder RSI values."""
    # Using period=3 for simple validation
    processor = RSIProcessor(period=3)
    processor.initialize()

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    base_ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Sequence of prices: 10.0 -> 11.0 -> 12.0 -> 13.0 -> 12.0
    prices = [10.0, 11.0, 12.0, 13.0, 12.0]

    # Period=3 requires 4 prices to produce the first RSI value
    # Price 0 (10.0)
    res0 = await processor.on_event(_make_bar_event(instrument, tf, prices[0], base_ts))
    assert res0 == []

    # Price 1 (11.0)
    res1 = await processor.on_event(_make_bar_event(instrument, tf, prices[1], base_ts))
    assert res1 == []

    # Price 2 (12.0)
    res2 = await processor.on_event(_make_bar_event(instrument, tf, prices[2], base_ts))
    assert res2 == []

    # Price 3 (13.0) -> First output (Initial SMA calculation)
    res3 = await processor.on_event(_make_bar_event(instrument, tf, prices[3], base_ts))
    assert len(res3) == 1
    assert res3[0].type == EventType.FEATURE
    assert res3[0].payload.feature == "rsi"
    assert res3[0].payload.value == pytest.approx(100.0)
    assert res3[0].payload.meta["signal"] == "overbought"

    # Price 4 (12.0) -> Second output (Welles Wilder exponential smoothing)
    # Gain: 0.0, Loss: 1.0
    # Smoothed avg gain: (1.0 * 2 + 0.0) / 3 = 0.6667
    # Smoothed avg loss: (0.0 * 2 + 1.0) / 3 = 0.3333
    # RS = 2.0 -> RSI = 100 - (100 / (1 + 2)) = 66.6667
    res4 = await processor.on_event(_make_bar_event(instrument, tf, prices[4], base_ts))
    assert len(res4) == 1
    assert res4[0].payload.value == pytest.approx(66.6667, rel=1e-4)
    assert res4[0].payload.meta["signal"] == "neutral"


@pytest.mark.asyncio
async def test_rsi_multi_instrument_isolation() -> None:
    """Verifies that prices from different instruments do not bleed into each other's states."""
    processor = RSIProcessor(period=3)
    processor.initialize()

    inst_a = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    inst_b = Instrument(symbol="MSFT", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # AAPL rises steadily, MSFT falls steadily
    # Feed 3 prices to both: no outputs yet
    for price_a, price_b in [(10.0, 100.0), (11.0, 99.0), (12.0, 98.0)]:
        assert await processor.on_event(_make_bar_event(inst_a, tf, price_a, ts)) == []
        assert await processor.on_event(_make_bar_event(inst_b, tf, price_b, ts)) == []

    # Feed 4th price: both should output correct initial values independently
    res_a = await processor.on_event(_make_bar_event(inst_a, tf, 13.0, ts))
    res_b = await processor.on_event(_make_bar_event(inst_b, tf, 97.0, ts))

    assert len(res_a) == 1
    assert res_a[0].payload.value == pytest.approx(100.0)  # AAPL has only gains

    assert len(res_b) == 1
    assert res_b[0].payload.value == pytest.approx(0.0)  # MSFT has only losses


@pytest.mark.asyncio
async def test_rsi_initialize_clears_state() -> None:
    """Verifies that calling initialize() clears all running states."""
    processor = RSIProcessor(period=3)
    processor.initialize()

    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Feed 3 bars
    for price in [10.0, 11.0, 12.0]:
        await processor.on_event(_make_bar_event(instrument, tf, price, ts))

    # Reset state
    processor.initialize()

    # Feed 3 more bars. If state wasn't cleared, the 4th global bar would produce an output.
    # Because state was cleared, it requires another 4 bars to get an output.
    assert await processor.on_event(_make_bar_event(instrument, tf, 13.0, ts)) == []
    assert await processor.on_event(_make_bar_event(instrument, tf, 14.0, ts)) == []
    assert await processor.on_event(_make_bar_event(instrument, tf, 15.0, ts)) == []

    # 4th bar after reset
    res = await processor.on_event(_make_bar_event(instrument, tf, 16.0, ts))
    assert len(res) == 1
    assert res[0].payload.value == pytest.approx(100.0)
