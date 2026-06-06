from __future__ import annotations

import asyncio
from datetime import datetime
from typing import AsyncIterator

from contracts import (
    Bar,
    Bus,
    Event,
    EventType,
    Instrument,
    MarketChannel,
    MarketDataService,
    MarketSourcePort,
    Quote,
    Subscription,
    Timeframe,
)


class MarketService(MarketDataService):
    """Aggregates market data adapters, manages subscription reuse, and deduplicates feeds."""

    def __init__(self, sources: list[MarketSourcePort], bus: Bus) -> None:
        """Initialize MarketService with market sources and event bus."""
        self.sources = sources
        self.bus = bus
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
        """Fetch historical bars for a given instrument and timeframe."""
        source = self._route(instrument)
        return await source.get_bars(instrument, timeframe, start, end)

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
                if chan == MarketChannel.QUOTES:
                    event_types.append(EventType.QUOTE)
                elif chan == MarketChannel.BARS:
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
                from loguru import logger

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
