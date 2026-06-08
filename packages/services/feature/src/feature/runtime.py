from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from contracts import Event, EventType, FeatureValue, Instrument, Timeframe
from contracts.ports import Bus, MarketDataService, Processor
from loguru import logger


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


class FeatureRuntime:
    """Manages the DAG of feature processors, historical warmups, and execution loops."""

    def __init__(self, bus: Bus, market: Optional[MarketDataService] = None) -> None:
        """Initialize FeatureRuntime with the event bus and optional market data service."""
        self.bus = bus
        self.market = market
        self.processors: Dict[str, Processor] = {}
        self.latest_values: dict[tuple[str, str], FeatureValue] = {}
        self._tasks: List[asyncio.Task] = []
        self._running = False

    def add_processor(self, processor: Processor) -> None:
        """Register a feature processor and wire it into the DAG."""
        self.processors[processor.name] = processor

    async def start(
        self,
        instruments: Optional[list[Instrument]] = None,
        timeframes: Optional[list[Timeframe]] = None,
    ) -> None:
        """Start the processing loop consuming from the bus and routing to processors."""
        if self._running:
            return

        if self.market is not None and instruments and timeframes:
            await self._warmup_processors(instruments, timeframes)

        self._running = True
        for processor in self.processors.values():
            task = asyncio.create_task(self._run_processor(processor))
            self._tasks.append(task)
        logger.info(f"FeatureRuntime started with {len(self.processors)} processors.")

    async def stop(self) -> None:
        """Stop the runtime loop."""
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        logger.info("FeatureRuntime stopped.")

    async def _warmup_processors(
        self,
        instruments: list[Instrument],
        timeframes: list[Timeframe],
    ) -> None:
        """Fetch historical bars and feed them to processors to warm up their internal state."""
        logger.info("Starting historical warmup for feature processors...")
        now = datetime.now(timezone.utc)

        for proc in self.processors.values():
            events_needed = proc.warmup_events
            if events_needed <= 0:
                continue

            for inst in instruments:
                for tf in timeframes:
                    try:
                        duration = _timeframe_duration(tf)
                        # Add a 20% safety margin to account for weekends/holidays where no bars exist
                        total_seconds = duration * events_needed * 1.2
                        start_time = now - timedelta(seconds=total_seconds)

                        logger.info(
                            "Warming up processor '{}' for {} ({}) requiring {} bars. Fetching from {}...",
                            proc.name,
                            inst.symbol,
                            tf.value,
                            events_needed,
                            start_time.isoformat(),
                        )

                        assert self.market is not None
                        bars = await self.market.get_bars(inst, tf, start_time, now)

                        warmed_count = 0
                        # Feed the bars in chronological order
                        for bar in sorted(bars, key=lambda b: b.ts_open):
                            event = Event(
                                type=EventType.BAR,
                                source="warmup",
                                payload=bar,
                                ts_event=bar.ts_open,
                            )
                            res = await proc.on_event(event)
                            for fe in res:
                                if fe.type == EventType.FEATURE:
                                    inst_key = (
                                        fe.payload.instrument.key
                                        if fe.payload.instrument
                                        else ""
                                    )
                                    self.latest_values[(proc.name, inst_key)] = (
                                        fe.payload
                                    )
                            warmed_count += 1

                        logger.info(
                            "Warmed up processor '{}' for {} ({}) with {} bars (needed {}).",
                            proc.name,
                            inst.symbol,
                            tf.value,
                            warmed_count,
                            events_needed,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to warm up processor '{}' for {} ({}): {}",
                            proc.name,
                            inst.symbol,
                            tf.value,
                            e,
                        )

    async def _run_processor(self, processor: Processor) -> None:
        """Background runner loop for a single processor."""
        try:
            async for event in self.bus.subscribe(processor.input):
                try:
                    feature_events = await processor.on_event(event)
                    for fe in feature_events:
                        if fe.type == EventType.FEATURE:
                            inst_key = (
                                fe.payload.instrument.key
                                if fe.payload.instrument
                                else ""
                            )
                            self.latest_values[(processor.name, inst_key)] = fe.payload
                        await self.bus.publish(fe)
                except Exception as e:
                    logger.error(
                        f"Processor '{processor.name}' failed to process event: {e}"
                    )
        except asyncio.CancelledError:
            # Normal task cancellation on shutdown
            pass
        except Exception as e:
            logger.error(
                f"Subscription loop for processor '{processor.name}' failed: {e}"
            )
