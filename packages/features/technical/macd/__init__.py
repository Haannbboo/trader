"""
features/technical/macd.py — MACDProcessor.

ONE instance == ONE factor definition ("macd"), not one-per-symbol. The market
adapter streams bars for MANY instruments through the bus; this single instance
keeps a separate EMA / signal-line state PER (instrument, timeframe) key
internally. So:
  - #instances == #factor kinds (macd, rsi, ...), NOT #factors x #symbols.
  - adding a symbol changes nothing here (a new bucket appears on first bar).
  - adding a factor = one more registered processor.

What the library does vs what THIS owns:
  - Standard MACD = EMA(fast) - EMA(slow) of close; signal = EMA(period) of
    the MACD line itself; histogram = macd - signal. The standard triple is
    12/26/9 but all three periods are configurable per processor instance.
  - exponential smoothing recurrences require maintaining running EMAs per
    instrument state to keep calculations O(1) and avoid passing large numpy
    arrays to TA libraries on every tick.
  - this Processor owns everything the library can't: subscribing to bars,
    maintaining per-key rolling state, warmup, attaching SEMANTICS
    (bullish/bearish), emitting a FeatureValue Event, and being deterministic
    + IO-free so replay == live. The library is a tool; the factor logic is
    ours.
"""

from __future__ import annotations

from collections import deque
from itertools import islice
from typing import Any

from contracts.ports import Subscription
from contracts.schema import (
    Bar,
    Event,
    EventType,
    FeatureValue,
)
from plugins import register


def _semantics(histogram: float) -> str:
    """The 'number + meaning' the agent reads from the histogram sign."""
    if histogram > 0:
        return "bullish"
    if histogram < 0:
        return "bearish"
    return "neutral"


class InstrumentMACDState:
    """Per-(instrument, timeframe) EMA + signal-line state.

    EMA seeds use the SMA of the first `period` closes, matching how
    pandas_ta / TA-Lib initialize the classical MACD. After seeding, the
    standard EMA recurrence applies.

    `closes` is bounded to `slow_period` because that's the largest history
    we ever need: the slow EMA's SMA seed reads the first `slow_period`
    closes, and from then on we only consume the most recent close for the
    recurrence. Bounding the deque (vs. an unbounded list) prevents the
    long-running live feed from growing memory without bound — see RSI for
    the parallel pattern (deque(maxlen=period+1)).
    """

    def __init__(self, fast_period: int, slow_period: int, signal_period: int) -> None:
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.closes: deque[float] = deque(maxlen=slow_period)
        self.ema_fast: float | None = None
        self.ema_slow: float | None = None
        self.signal: float | None = None
        self.macd_values: list[float] = []  # bounded to signal_period for the seed


@register("feature", "macd")
class MACDProcessor:  # implements ports.Processor
    name = "macd"

    def __init__(
        self,
        *,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        **params: Any,
    ) -> None:
        if fast_period < 1 or slow_period < 1 or signal_period < 1:
            raise ValueError("MACD periods must be positive integers")
        if fast_period >= slow_period:
            raise ValueError(
                "fast_period must be < slow_period (got "
                f"fast={fast_period}, slow={slow_period})"
            )
        self._fast_period = fast_period
        self._slow_period = slow_period
        self._signal_period = signal_period
        # Multiplier = 2 / (period + 1) for the standard EMA recurrence.
        self._k_fast = 2.0 / (fast_period + 1)
        self._k_slow = 2.0 / (slow_period + 1)
        self._k_signal = 2.0 / (signal_period + 1)
        # Combined state container: (instrument.key, timeframe.value) -> state.
        self._states: dict[tuple[str, str], InstrumentMACDState] = {}

    @property
    def input(self) -> Subscription:
        """Consumes BAR events for all instruments."""
        return Subscription(event_types=(EventType.BAR,))

    @property
    def warmup_events(self) -> int:
        """Warmup events required for both EMAs AND the signal line to
        converge. The slow EMA needs `slow_period` bars; the signal EMA
        then needs `signal_period` MACD values on top of that. The runtime
        multiplies this by 1.2 for a safety margin (weekends/holidays)."""
        return (self._slow_period + self._signal_period) * 10

    def initialize(self, config: dict[str, Any] | None = None) -> None:
        """Reset state before starting the event stream, optionally updating
        configuration.

        If `config` provides any of `fast_period`, `slow_period`, or
        `signal_period`, all three are re-validated as a triple and the EMA
        multipliers (`_k_*`) are recomputed. Pass a complete triple to avoid
        surprises — a partial dict applies the supplied keys and leaves the
        rest at their current values. Mirrors RSI's mutable-init pattern
        so the registry's `cls(); initialize(params)` handoff Just Works."""
        if config and any(
            k in config for k in ("fast_period", "slow_period", "signal_period")
        ):
            new_fast = int(config.get("fast_period", self._fast_period))
            new_slow = int(config.get("slow_period", self._slow_period))
            new_sig = int(config.get("signal_period", self._signal_period))
            if new_fast < 1 or new_slow < 1 or new_sig < 1:
                raise ValueError("MACD periods must be positive integers")
            if new_fast >= new_slow:
                raise ValueError(
                    "fast_period must be < slow_period (got "
                    f"fast={new_fast}, slow={new_slow})"
                )
            self._fast_period = new_fast
            self._slow_period = new_slow
            self._signal_period = new_sig
            self._k_fast = 2.0 / (new_fast + 1)
            self._k_slow = 2.0 / (new_slow + 1)
            self._k_signal = 2.0 / (new_sig + 1)
        self._states.clear()

    async def on_event(self, event: Event) -> list[Event]:
        """Called once per matching BAR. Returns 0 or 1 FEATURE events."""
        if event.type != EventType.BAR:
            return []

        bar: Bar = event.payload
        key = (bar.instrument.key, bar.timeframe.value)

        state = self._states.get(key)
        if state is None:
            state = InstrumentMACDState(
                self._fast_period, self._slow_period, self._signal_period
            )
            self._states[key] = state

        state.closes.append(float(bar.close))
        close = float(bar.close)

        # --- Seed fast EMA (need `fast_period` closes) ---
        if state.ema_fast is None and len(state.closes) >= self._fast_period:
            state.ema_fast = (
                sum(islice(state.closes, self._fast_period)) / self._fast_period
            )

        # --- Seed slow EMA (need `slow_period` closes) ---
        if state.ema_slow is None and len(state.closes) >= self._slow_period:
            state.ema_slow = (
                sum(islice(state.closes, self._slow_period)) / self._slow_period
            )

        # Advance each EMA independently from its own seed point. This
        # matches pandas_ta's behavior: each EMA is computed against the
        # full input series, not gated on the other EMA's existence. We
        # still can't compute a MACD line until BOTH EMAs are seeded.
        if state.ema_fast is not None:
            state.ema_fast = (close - state.ema_fast) * self._k_fast + state.ema_fast
        if state.ema_slow is not None:
            state.ema_slow = (close - state.ema_slow) * self._k_slow + state.ema_slow

        if state.ema_fast is None or state.ema_slow is None:
            return []

        macd_line = state.ema_fast - state.ema_slow

        # --- Seed signal EMA (need `signal_period` MACD values) ---
        if state.signal is None:
            state.macd_values.append(macd_line)
            if len(state.macd_values) >= self._signal_period:
                state.signal = sum(state.macd_values) / self._signal_period
            return []

        # Recurrence for the signal line.
        state.signal = (macd_line - state.signal) * self._k_signal + state.signal

        histogram = macd_line - state.signal

        fv = FeatureValue(
            feature=self.name,
            value=macd_line,
            ts_event=bar.ts_open,
            instrument=bar.instrument,
            window=(
                f"fast={self._fast_period} slow={self._slow_period} "
                f"signal={self._signal_period} tf={bar.timeframe.value}"
            ),
            meta={
                "signal_line": state.signal,
                "histogram": histogram,
                "signal": _semantics(histogram),
            },
        )

        return [
            Event(
                type=EventType.FEATURE,
                source=f"feature:{self.name}",
                payload=fv,
                ts_event=bar.ts_open,
                ts_ingest=event.ts_ingest,
            )
        ]
