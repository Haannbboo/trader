"""Unit tests for MACDProcessor.

MACD (Moving Average Convergence Divergence) is the difference between a fast
EMA and a slow EMA of close prices, with a signal line = EMA of MACD itself.
Standard: EMA12 - EMA26, signal = 9-period EMA of MACD, histogram = MACD - signal.

These tests pin the math, the per-instrument state isolation, and the
meta-channel contract (signal_line, histogram, signal semantics).
"""

from collections import deque
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
from features.technical.macd import InstrumentMACDState, MACDProcessor


def _bar_event(
    instrument: Instrument,
    timeframe: Timeframe,
    close: float,
    ts: datetime,
) -> Event:
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
    return Event(type=EventType.BAR, source="test", payload=bar, ts_event=ts)


async def _drive(
    processor: MACDProcessor,
    instrument: Instrument,
    tf: Timeframe,
    prices: list[float],
    ts: datetime,
) -> list[Event]:
    """Sequentially feed bars; return all emitted FEATURE events."""
    emitted: list[Event] = []
    for p in prices:
        out = await processor.on_event(_bar_event(instrument, tf, p, ts))
        emitted.extend(out)
    return emitted


def _flat_series(n: int, base: float = 100.0) -> list[float]:
    """A constant-price series. EMA_fast == EMA_slow == base forever, so
    MACD == 0 and signal == 0 and histogram == 0."""
    return [base] * n


def _rising_series(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + i * step for i in range(n)]


@pytest.mark.asyncio
async def test_macd_flat_series_stays_at_zero() -> None:
    """A constant-price feed produces MACD=0, signal=0, histogram=0, 'neutral'."""
    processor = MACDProcessor(fast_period=12, slow_period=26, signal_period=9)
    processor.initialize()

    inst = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Flat series: no MACD divergence at all. We need >= slow_period + signal_period
    # bars to fully warm up the signal line.
    prices = _flat_series(60)
    emitted = await _drive(processor, inst, tf, prices, ts)

    # All emitted FEATURE events post-warmup should report zero MACD.
    assert emitted, "expected at least one emission on a 60-bar flat series"
    for ev in emitted:
        assert ev.type == EventType.FEATURE
        assert ev.payload.feature == "macd"
        assert ev.payload.value == pytest.approx(0.0, abs=1e-9)
        assert ev.payload.meta["signal_line"] == pytest.approx(0.0, abs=1e-9)
        assert ev.payload.meta["histogram"] == pytest.approx(0.0, abs=1e-9)
        assert ev.payload.meta["signal"] == "neutral"


@pytest.mark.asyncio
async def test_macd_rising_series_becomes_bullish() -> None:
    """A steady uptrend produces a positive MACD line (fast EMA > slow EMA)
    and once the signal EMA warms, a positive histogram => 'bullish'."""
    processor = MACDProcessor(fast_period=12, slow_period=26, signal_period=9)
    processor.initialize()

    inst = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Rising: MACD line is positive throughout, so once signal EMA converges
    # the histogram is also positive. Use a long-enough series.
    prices = _rising_series(80, start=100.0, step=1.0)
    emitted = await _drive(processor, inst, tf, prices, ts)

    assert emitted
    last = emitted[-1].payload
    # Fast EMA > slow EMA on a rising series => MACD > 0
    assert last.value > 0
    # Signal line is an EMA of a positive series => > 0
    assert last.meta["signal_line"] > 0
    # Histogram = MACD - signal. In a steady trend, MACD converges above the
    # signal, so histogram > 0.
    assert last.meta["histogram"] == pytest.approx(
        last.value - last.meta["signal_line"], abs=1e-9
    )
    assert last.meta["signal"] == "bullish"


@pytest.mark.asyncio
async def test_macd_falling_series_becomes_bearish() -> None:
    """A steady downtrend produces negative MACD, negative histogram, 'bearish'."""
    processor = MACDProcessor(fast_period=12, slow_period=26, signal_period=9)
    processor.initialize()

    inst = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    prices = _rising_series(80, start=200.0, step=-1.0)  # descending
    emitted = await _drive(processor, inst, tf, prices, ts)

    assert emitted
    last = emitted[-1].payload
    assert last.value < 0
    assert last.meta["signal_line"] < 0
    assert last.meta["histogram"] < 0
    assert last.meta["signal"] == "bearish"


@pytest.mark.asyncio
async def test_macd_no_emit_during_warmup() -> None:
    """No FEATURE events should be emitted until both EMAs and the signal
    line have all converged. For 12/26/9 the minimum bar count is 26 + 9 = 35."""
    processor = MACDProcessor(fast_period=12, slow_period=26, signal_period=9)
    processor.initialize()

    inst = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # 34 bars: short of the 35-bar minimum.
    emitted = await _drive(processor, inst, tf, _rising_series(34), ts)
    assert emitted == []


@pytest.mark.asyncio
async def test_macd_multi_instrument_isolation() -> None:
    """Bars from different (instrument, timeframe) keys must not bleed."""
    processor = MACDProcessor(fast_period=12, slow_period=26, signal_period=9)
    processor.initialize()

    aapl = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    msft = Instrument(symbol="MSFT", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Interleave: AAPL rising, MSFT flat. After warmup, AAPL's MACD > 0,
    # MSFT's MACD == 0.
    rising = _rising_series(60, start=100.0, step=1.0)
    flat = _flat_series(60, base=500.0)

    aapl_emit: list[Event] = []
    msft_emit: list[Event] = []
    for pa, pm in zip(rising, flat):
        aapl_emit.extend(await processor.on_event(_bar_event(aapl, tf, pa, ts)))
        msft_emit.extend(await processor.on_event(_bar_event(msft, tf, pm, ts)))

    assert aapl_emit, "AAPL should have produced at least one FEATURE event"
    assert msft_emit, "MSFT should have produced at least one FEATURE event"

    assert aapl_emit[-1].payload.value > 0
    assert msft_emit[-1].payload.value == pytest.approx(0.0, abs=1e-9)


@pytest.mark.asyncio
async def test_macd_initialize_clears_state() -> None:
    """initialize() must wipe the per-key state dict."""
    processor = MACDProcessor(fast_period=12, slow_period=26, signal_period=9)
    processor.initialize()

    inst = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Warm up.
    await _drive(processor, inst, tf, _rising_series(40), ts)

    # Reset.
    processor.initialize()
    assert processor._states == {}

    # 34 bars after reset: still in warmup, no emission.
    emitted = await _drive(processor, inst, tf, _rising_series(34), ts)
    assert emitted == []


@pytest.mark.asyncio
async def test_macd_closes_history_is_bounded() -> None:
    """`state.closes` must be a bounded deque, not an unbounded list. Drives
    well past `slow_period` bars and asserts the deque length stays pinned
    at `slow_period` and the contents are the most recent closes (the
    seed-time closes are dropped once the deque fills)."""
    fast, slow, sig = 3, 6, 2
    processor = MACDProcessor(fast_period=fast, slow_period=slow, signal_period=sig)
    processor.initialize()

    inst = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Drive 100 bars — well past slow_period.
    prices = _rising_series(100, start=100.0, step=1.0)
    await _drive(processor, inst, tf, prices, ts)

    state = processor._states[(inst.key, tf.value)]
    assert isinstance(
        state.closes, deque
    ), f"state.closes must be a deque (got {type(state.closes).__name__})"
    assert (
        state.closes.maxlen == slow
    ), f"deque maxlen must equal slow_period (got {state.closes.maxlen})"
    assert (
        len(state.closes) == slow
    ), f"deque length must stay pinned at slow_period (got {len(state.closes)})"
    # Contents must be the most recent `slow` closes, not the seed-time
    # ones. The 100-bar series ends at prices[99] = 199; the last `slow`
    # closes are prices[100-slow:] = [194, 195, 196, 197, 198, 199].
    assert list(state.closes) == prices[-slow:]


def test_macd_initialize_applies_periods_from_config() -> None:
    """initialize(config) must mutate periods AND recompute the EMA multipliers
    so the runtime registry's cls(); initialize(params) handoff produces a
    processor that emits under the new windows."""
    processor = MACDProcessor()  # defaults 12/26/9
    assert processor._fast_period == 12
    assert processor._slow_period == 26
    assert processor._signal_period == 9
    assert processor._k_fast == pytest.approx(2.0 / 13)
    assert processor._k_slow == pytest.approx(2.0 / 27)
    assert processor._k_signal == pytest.approx(2.0 / 10)

    processor.initialize({"fast_period": 5, "slow_period": 10, "signal_period": 3})

    assert processor._fast_period == 5
    assert processor._slow_period == 10
    assert processor._signal_period == 3
    assert processor._k_fast == pytest.approx(2.0 / 6)
    assert processor._k_slow == pytest.approx(2.0 / 11)
    assert processor._k_signal == pytest.approx(2.0 / 4)
    # warmup_events should reflect the new (slow + signal) * 10.
    assert processor.warmup_events == (10 + 3) * 10


def test_macd_initialize_partial_config_keeps_unspecified_periods() -> None:
    """A partial config (e.g. only fast_period) must apply the supplied keys
    and leave the rest at their current values — not silently zero them."""
    processor = MACDProcessor()
    processor.initialize({"fast_period": 6})
    assert processor._fast_period == 6
    assert processor._slow_period == 26  # unchanged
    assert processor._signal_period == 9  # unchanged
    # Only the fast multiplier should have been recomputed.
    assert processor._k_fast == pytest.approx(2.0 / 7)
    assert processor._k_slow == pytest.approx(2.0 / 27)
    assert processor._k_signal == pytest.approx(2.0 / 10)


def test_macd_initialize_rejects_invalid_periods() -> None:
    """A config with bad periods (zero, negative, or fast >= slow) must
    raise rather than producing a silently-broken processor."""
    processor = MACDProcessor()

    with pytest.raises(ValueError, match="must be positive"):
        processor.initialize({"fast_period": 0, "slow_period": 26, "signal_period": 9})

    with pytest.raises(ValueError, match="must be positive"):
        processor.initialize(
            {"fast_period": 12, "slow_period": 26, "signal_period": -1}
        )

    with pytest.raises(ValueError, match="must be < slow_period"):
        processor.initialize({"fast_period": 26, "slow_period": 12, "signal_period": 9})

    # Original periods should be unchanged after a failed initialize().
    assert processor._fast_period == 12
    assert processor._slow_period == 26
    assert processor._signal_period == 9


def test_macd_initialize_with_no_config_is_a_pure_reset() -> None:
    """initialize(None) (or initialize({})) must leave periods alone and just
    clear state. This is the warmup path the runtime takes at boot."""
    processor = MACDProcessor()
    # Pre-populate state.
    processor._states[("equity:AAPL", "1m")] = InstrumentMACDState(12, 26, 9)

    processor.initialize()  # no config

    assert processor._states == {}
    assert processor._fast_period == 12
    assert processor._slow_period == 26
    assert processor._signal_period == 9


@pytest.mark.asyncio
async def test_macd_initialize_periods_take_effect_on_next_event() -> None:
    """End-to-end: build a processor with default periods, then call
    initialize({fast, slow, signal}). The next event window string should
    reflect the new periods — proving the change actually flowed through
    to the emitter, not just the internal fields."""
    processor = MACDProcessor()
    processor.initialize({"fast_period": 3, "slow_period": 6, "signal_period": 2})

    inst = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Need slow (6) + signal (2) = 8 bars to converge; give 10.
    emitted = await _drive(processor, inst, tf, _rising_series(10), ts)
    assert emitted, "expected at least one emission after initialize()"
    last = emitted[-1].payload
    assert last.window is not None
    assert "fast=3" in last.window
    assert "slow=6" in last.window
    assert "signal=2" in last.window


@pytest.mark.asyncio
async def test_macd_value_matches_hand_computed_ema() -> None:
    """Property check: a perfectly linear series with slope b is a fixed
    point of each EMA individually, but the two EMAs settle to lag-adjusted
    lines that differ by a known constant:

        macd_line -> ((1 - k_slow) / k_slow - (1 - k_fast) / k_fast) * b

    For k_fast = 0.5 and k_slow = 2/7 (the 3- and 6-period multipliers) on a
    series of slope 1, that constant is (2.5 - 1.0) * 1 = 1.5. Once the
    signal EMA has also converged to that constant MACD value, the
    histogram -> 0. This pin-tests the recurrence without hand-coding the
    bar-by-bar advance (which is easy to get wrong by one bar)."""
    fast, slow, sig = 3, 6, 2
    processor = MACDProcessor(fast_period=fast, slow_period=slow, signal_period=sig)
    processor.initialize()

    inst = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    tf = Timeframe.M1
    ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)

    # Perfectly linear: 100, 101, 102, ...
    n = 60
    prices = [100.0 + i for i in range(n)]
    emitted = await _drive(processor, inst, tf, prices, ts)

    assert emitted, "expected at least one emission on a 60-bar linear series"
    last = emitted[-1].payload
    # Tolerance accounts for the SMA-seed transient and float drift.
    assert last.value == pytest.approx(1.5, abs=1e-3)
    # The signal EMA should also have converged to ~1.5 by bar 60, so the
    # histogram -> 0. (Don't assert a 'neutral' semantic label here — at
    # machine precision the residual histogram is positive, and the
    # 'bullish'/'bearish' check is exercised by the trend tests above.)
    assert last.meta["histogram"] == pytest.approx(0.0, abs=1e-3)
