"""
features/technical/rsi.py — RSIProcessor.

ONE instance == ONE factor definition ("rsi_14"), not one-per-symbol. The market
adapter streams bars for MANY instruments through the bus; this single instance
keeps a separate rolling window PER (instrument, timeframe) key internally. So:
  - #instances == #factor kinds (rsi_14, macd, ...), NOT #factors × #symbols.
  - adding a symbol changes nothing here (a new bucket appears on first bar).
  - adding a factor = one more registered processor.

What the library does vs what THIS owns:
  - Welles Wilder's formula uses exponential smoothing recurrence, which requires
    maintaining running averages (avg_gain, avg_loss) per instrument state to keep
    calculations O(1) and avoid the memory/performance overhead of passing large
    numpy arrays to external TA libraries on every tick.
  - this Processor owns everything the library can't: subscribing to bars,
    maintaining per-key rolling state, warmup, attaching SEMANTICS (overbought/
    oversold), emitting a FeatureValue Event, and being deterministic + IO-free
    so replay == live. The library is a tool; the factor logic is ours.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from contracts.ports import Subscription
from contracts.schema import (
    Bar,
    Event,
    EventType,
    FeatureValue,
)
from plugins import register


def _semantics(rsi: float) -> str:
    """The 'number + meaning' the agent reads."""
    if rsi >= 70:
        return "overbought"
    if rsi <= 30:
        return "oversold"
    return "neutral"


class InstrumentRSIState:
    """Encapsulates the rolling price history and running averages for an instrument."""

    def __init__(self, period: int) -> None:
        self.prices: deque[float] = deque(maxlen=period + 1)
        self.avg_gain: float | None = None
        self.avg_loss: float | None = None


@register("feature", "rsi")
class RSIProcessor:  # implements ports.Processor
    name = "rsi"

    def __init__(self, *, period: int = 14, **params) -> None:
        self._period = period
        # Combined state container: (instrument.key, timeframe.value) -> InstrumentRSIState
        self._states: dict[tuple[str, str], InstrumentRSIState] = {}

    @property
    def input(self) -> Subscription:
        """Consumes BAR events for all instruments."""
        return Subscription(event_types=(EventType.BAR,))

    @property
    def warmup_events(self) -> int:
        """Warmup events required to converge the exponential smoothing."""
        return self._period * 10

    def initialize(self, config: dict[str, Any] | None = None) -> None:
        """Reset state before starting the event stream, optionally updating configuration."""
        if config:
            self._period = int(config.get("period", self._period))
        self._states.clear()

    async def on_event(self, event: Event) -> list[Event]:
        """Called once per matching BAR."""
        if event.type != EventType.BAR:
            return []

        bar: Bar = event.payload
        key = (bar.instrument.key, bar.timeframe.value)

        # Get or create state for the instrument/timeframe
        if key not in self._states:
            self._states[key] = InstrumentRSIState(self._period)

        state = self._states[key]
        state.prices.append(float(bar.close))

        if len(state.prices) < 2:
            return []

        # Calculate latest changes
        change = state.prices[-1] - state.prices[-2]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        # Warm up until we have the required closes (period + 1 prices)
        if len(state.prices) < self._period + 1:
            return []

        # Compute smoothed averages
        if state.avg_gain is None or state.avg_loss is None:
            # First-time calculation (SMA initialization)
            changes = [
                state.prices[i] - state.prices[i - 1]
                for i in range(1, len(state.prices))
            ]
            gains = [c for c in changes if c > 0]
            losses = [-c for c in changes if c < 0]

            state.avg_gain = sum(gains) / self._period
            state.avg_loss = sum(losses) / self._period
        else:
            # Wilder's exponential smoothing recurrence
            state.avg_gain = (state.avg_gain * (self._period - 1) + gain) / self._period
            state.avg_loss = (state.avg_loss * (self._period - 1) + loss) / self._period

        # Compute RSI
        if state.avg_loss == 0:
            rsi = 100.0 if state.avg_gain > 0 else 50.0
        else:
            rs = state.avg_gain / state.avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        fv = FeatureValue(
            feature=self.name,
            value=rsi,
            ts_event=bar.ts_open,
            instrument=bar.instrument,
            window=f"period={self._period} tf={bar.timeframe.value}",
            meta={"signal": _semantics(rsi)},
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
