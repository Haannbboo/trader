from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional

from contracts import (
    AssetClass,
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
    occ_to_instrument,
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
        self._pump_tasks: dict[
            MarketSourcePort,
            tuple[asyncio.Task, list[Instrument], list[MarketChannel]],
        ] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the market data service and connect all sources."""
        for source in self.sources:
            await source.start()

    async def run(self) -> None:
        """Subscribe to configured instruments on startup and keep the subscription loops alive.

        Runs forever (until cancelled on shutdown).
        """
        tasks = []
        for source in self.sources:
            params = getattr(source, "params", {})
            symbols = params.get("instruments", [])
            if not symbols:
                continue

            asset_classes = source.capabilities.asset_classes
            if not asset_classes:
                continue
            primary_class = asset_classes[0]

            instruments = []
            for sym in symbols:
                try:
                    if primary_class == AssetClass.OPTION:
                        inst = occ_to_instrument(sym)
                    else:
                        inst = Instrument(symbol=sym, asset_class=primary_class)
                    instruments.append(inst)
                except Exception as e:
                    logger.error(
                        "Failed to parse instrument {} for source {}: {}",
                        sym,
                        source.name,
                        e,
                    )

            if not instruments:
                continue

            # Define the background pump loop for this source's instruments
            async def run_pump(insts=instruments, src_name=source.name):
                try:
                    async for _ in self.subscribe(insts, [MarketChannel.BARS]):
                        pass
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.exception(
                        "Market subscription pump failed for {}: {}", src_name, e
                    )

            task = asyncio.create_task(
                run_pump(),
                name=f"market-pump-{source.name}",
            )
            tasks.append(task)

        if not tasks:
            # Sleep forever (until cancelled) to align with long-running service task pattern
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            return

        # Keep running until cancelled
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def stop(self) -> None:
        """Stop the market data service and disconnect all sources."""
        async with self._lock:
            for task, _, _ in self._pump_tasks.values():
                task.cancel()
            if self._pump_tasks:
                await asyncio.gather(
                    *(task for task, _, _ in self._pump_tasks.values()),
                    return_exceptions=True,
                )
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
        async with self._lock:
            # Validate routeability up front so unroutable instruments raise
            # immediately at the call site, before any ref count is taken.
            # _route() is a pure read of self.sources / source.capabilities —
            # no I/O, no mutation — so this is safe to call under the lock.
            for inst in instruments:
                self._route(inst)
            for inst in instruments:
                for chan in channels:
                    key = (inst, chan)
                    self._ref_counts[key] = self._ref_counts.get(key, 0) + 1
            await self._update_subscriptions()

        try:
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
            async with self._lock:
                for inst in instruments:
                    for chan in channels:
                        key = (inst, chan)
                        if key in self._ref_counts:
                            self._ref_counts[key] -= 1
                            if self._ref_counts[key] <= 0:
                                del self._ref_counts[key]
                await self._update_subscriptions()

    def _route(self, instrument: Instrument) -> MarketSourcePort:
        """Pick a source by declared capabilities (+ failover order), not by name."""
        for source in self.sources:
            if instrument.asset_class in source.capabilities.asset_classes:
                return source
        raise ValueError(
            f"No market source found that supports asset class: {instrument.asset_class}"
        )

    async def _update_subscriptions(self) -> None:
        """Recalculate and update the active pump tasks for each source based on ref counts."""
        # Group active (instrument, channel) by source
        active_by_source: dict[
            MarketSourcePort, list[tuple[Instrument, MarketChannel]]
        ] = {}
        for source in self.sources:
            active_by_source[source] = []

        for (inst, chan), count in self._ref_counts.items():
            if count > 0:
                # All entries in _ref_counts have already been routed by
                # subscribe() under the lock, so this should never raise.
                # If it does, surface the error rather than silently dropping
                # the instrument — a caller waiting on a bus subscription
                # that no pump is feeding will hang forever.
                source = self._route(inst)
                active_by_source[source].append((inst, chan))

        # For each source, check if the subscription needs to be updated
        for source, active_pairs in active_by_source.items():
            current_task_info = self._pump_tasks.get(source)

            if not active_pairs:
                if current_task_info is not None:
                    task, _, _ = current_task_info
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    del self._pump_tasks[source]
                continue

            # Extract unique instruments and channels
            new_insts = list({pair[0] for pair in active_pairs})
            new_chans = list({pair[1] for pair in active_pairs})

            # Check if we already have a task running with the exact same subscription
            if current_task_info is not None:
                _, running_insts, running_chans = current_task_info
                if set(new_insts) == set(running_insts) and set(new_chans) == set(
                    running_chans
                ):
                    continue

                task, _, _ = current_task_info
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            insts_to_sub = list(new_insts)
            chans_to_sub = list(new_chans)

            async def pump(s=source, insts=insts_to_sub, chans=chans_to_sub) -> None:
                try:
                    async for event in s.subscribe(insts, chans):
                        await self.bus.publish(event)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.exception(
                        "Upstream stream error for source {} (instruments={}, channels={}): {}",
                        s.name,
                        [i.symbol for i in insts],
                        [c.value for c in chans],
                        e,
                    )

            task = asyncio.create_task(
                pump(),
                name=f"pump-source-{source.name}",
            )
            self._pump_tasks[source] = (task, insts_to_sub, chans_to_sub)

    def _multiplex(self, instrument: Instrument) -> AsyncIterator[Event]:
        """One upstream subscription per instrument, fanned out to all callers."""
        return self.subscribe([instrument], list(MarketChannel))
