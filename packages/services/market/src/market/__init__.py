from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional

from contracts import (
    Bar,
    Bus,
    Event,
    EventType,
    HistoryStore,
    Instrument,
    MarketChannel,
    MarketDataService,
    MarketSourcePort,
    Quote,
    Subscription,
    Timeframe,
)
from loguru import logger
from persistence import DbWriter


def _timeframe_duration(tf: Timeframe) -> float:
    """Helper to convert Timeframe enum/value into duration in seconds.

    Raises:
        ValueError: If the timeframe unit is unsupported.
    """
    val = tf.value
    unit = val[-1]
    num = int(val[:-1])
    if unit == "s":
        return float(num)
    elif unit == "m":
        return float(num * 60)
    elif unit == "h":
        return float(num * 3600)
    elif unit == "d":
        return float(num * 86400)
    raise ValueError(f"Unsupported timeframe unit: {unit!r} (from {tf})")


class MarketService(MarketDataService):
    """Aggregates market data adapters, manages subscription reuse, and deduplicates feeds."""

    def __init__(
        self,
        sources: list[MarketSourcePort],
        bus: Bus,
        repository: Optional[HistoryStore] = None,
        writer: Optional[DbWriter] = None,
    ) -> None:
        """Initialize MarketService with market sources, event bus, and optional repository/writer."""
        self.sources = sources
        self.bus = bus
        self._repository = repository
        self._writer = writer
        self._ref_counts: dict[tuple[Instrument, MarketChannel], int] = {}
        self._pump_tasks: dict[tuple[Instrument, MarketChannel], asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the market data service and connect all sources."""
        for source in self.sources:
            await source.start()

    async def stop(self) -> None:
        """Stop the market data service and disconnect all sources."""
        async with self._lock:
            for task in self._pump_tasks.values():
                task.cancel()
            if self._pump_tasks:
                await asyncio.gather(*self._pump_tasks.values(), return_exceptions=True)
            self._pump_tasks.clear()
            self._ref_counts.clear()

        for source in self.sources:
            await source.stop()

    async def get_quote(self, instrument: Instrument) -> Quote:
        """Fetch the latest quote for a given instrument."""
        source = self._route(instrument)
        return await source.get_quote(instrument)

    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Fetch historical bars for a given instrument and timeframe.

        Checks the local database first if present. If bars are incomplete or missing
        for the requested range, queries the source adapter and fills the local DB.
        """
        source = self._route(instrument)

        if self._repository is None or self._writer is None:
            return await source.get_bars(instrument, timeframe, start, end)

        # 1. Query the local database
        local_bars = await self._repository.fetch_bars(
            instrument, timeframe, start, end
        )

        # 2. Check coverage: are any expected bars missing?
        # NOTE: We use a simple boundary check (first and last bar) rather than
        # counting bars or verifying spacing. Any logic that inspects the bars
        # themselves (whether checking counts or looping through timestamps to
        # find gaps) will always treat natural data gaps (weekends, holidays, or
        # illiquid periods) as missing fetches, causing permanent cache-miss loops.
        # A future improvement to eliminate these false positives is to track
        # successfully fetched intervals in a metadata table.
        missing = True
        if local_bars:
            duration = _timeframe_duration(timeframe)
            first_ok = local_bars[0].ts_open <= start
            last_ok = local_bars[-1].ts_open + timedelta(seconds=duration) >= end
            if first_ok and last_ok:
                missing = False

        if not missing:
            return local_bars

        # 3. Cache miss: fetch from live market source and upsert
        logger.info(
            "Local cache miss for {} [{}, {}] on timeframe {}. Fetching from live adapter.",
            instrument.symbol,
            start,
            end,
            timeframe.value,
        )
        bars = await source.get_bars(instrument, timeframe, start, end)

        if bars:
            await self._writer.store_bars(bars, source.name)

        return bars

    async def subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]:
        """Subscribe to real-time streams (quotes, trades, bars) for instruments."""
        # 1. Register active interest and start background pump tasks.
        # Under self._lock, we increment reference counts for each (instrument, channel).
        # If a count goes from 0 -> 1, it means this is the first subscriber for this stream,
        # so we spin up a new background pump task to read from the adapter and publish to the bus.
        async with self._lock:
            for inst in instruments:
                for chan in channels:
                    key = (inst, chan)
                    self._ref_counts[key] = self._ref_counts.get(key, 0) + 1
                    if self._ref_counts[key] == 1:
                        await self._start_pump(inst, chan)

        try:
            # 2. Yield events from the Bus.
            # Map requested MarketChannel enums to their corresponding EventType enums.
            event_types = []
            for chan in channels:
                if chan in (MarketChannel.QUOTES, MarketChannel.TRADES):
                    if EventType.QUOTE not in event_types:
                        event_types.append(EventType.QUOTE)
                elif chan == MarketChannel.BARS:
                    if EventType.BAR not in event_types:
                        event_types.append(EventType.BAR)

            sub = Subscription(
                instruments=tuple(instruments),
                event_types=tuple(event_types),
            )

            # The Bus handles all fan-out, queue management, and cleanup per caller.
            async for event in self.bus.subscribe(sub):
                yield event
        finally:
            # 3. Clean up active subscriptions when the caller exits or cancels.
            # Under self._lock, we decrement reference counts. If the count drops to 0,
            # no active strategies or subagents need this stream anymore, so we cancel
            # and clean up the background pump task.
            async with self._lock:
                for inst in instruments:
                    for chan in channels:
                        key = (inst, chan)
                        if key in self._ref_counts:
                            self._ref_counts[key] -= 1
                            if self._ref_counts[key] <= 0:
                                del self._ref_counts[key]
                                await self._stop_pump(inst, chan)

    def _route(self, instrument: Instrument) -> MarketSourcePort:
        """Pick a source by declared capabilities (+ failover order), not by name."""
        for source in self.sources:
            if instrument.asset_class in source.capabilities.asset_classes:
                return source
        raise ValueError(
            f"No market source found that supports asset class: {instrument.asset_class}"
        )

    async def _start_pump(self, instrument: Instrument, channel: MarketChannel) -> None:
        source = self._route(instrument)
        key = (instrument, channel)

        async def pump() -> None:
            try:
                async for event in source.subscribe([instrument], [channel]):
                    await self.bus.publish(event)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.exception(
                    f"Upstream stream error for {instrument.symbol} {channel.value}: {e}"
                )

        self._pump_tasks[key] = asyncio.create_task(
            pump(), name=f"pump-{instrument.symbol}-{channel.value}"
        )

    async def _stop_pump(self, instrument: Instrument, channel: MarketChannel) -> None:
        key = (instrument, channel)
        if task := self._pump_tasks.pop(key, None):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _multiplex(self, instrument: Instrument) -> AsyncIterator[Event]:
        """One upstream subscription per instrument, fanned out to all callers."""
        return self.subscribe([instrument], list(MarketChannel))
