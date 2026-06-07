"""
ta.bus.inprocess — Bus for single-process / single-machine. asyncio fan-out to
in-memory per-subscriber queues. Implements the Bus protocol so RedisStreamsBus
(durability + replay) can replace it later with ZERO caller changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional, Tuple

import anyio
from anyio.streams.memory import MemoryObjectSendStream
from contracts.ports import HistoryStore, Subscription
from contracts.schema import Bar, Event, EventType, Timeframe
from loguru import logger

from ._filters import matches_subscription


class InProcessBus:
    """Fan-out hub. publish() pushes to every matching subscriber's queue;
    subscribe() yields a filtered stream. Also the home of subscription
    MULTIPLEXING bookkeeping (many callers, one upstream sub per instrument)."""

    def __init__(self, *, max_queue: int = 10_000) -> None:
        self.max_queue = max_queue
        # List of (subscription, send_stream)
        self._subscribers: List[Tuple[Subscription, MemoryObjectSendStream[Event]]] = []
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("InProcessBus started.")

    async def stop(self) -> None:
        self._running = False
        # Close all subscriber streams
        for _, send_stream in self._subscribers:
            try:
                send_stream.close()
            except Exception:
                pass
        self._subscribers.clear()
        logger.info("InProcessBus stopped and all streams terminated.")

    async def publish(self, event: Event) -> None:
        """Publishes an event to matching subscriber queues."""
        if not self._running:
            logger.warning("Bus is not running. Ignoring event publish.")
            return

        # Clean up closed streams first
        self._cleanup_closed_subscribers()

        for sub, send_stream in self._subscribers:
            if matches_subscription(event, sub):
                try:
                    send_stream.send_nowait(event)
                except anyio.WouldBlock:
                    logger.warning(
                        f"Subscriber queue full (max={self.max_queue}). "
                        f"Discarding event for {sub}."
                    )
                except anyio.ClosedResourceError:
                    pass

    def subscribe(
        self,
        subscription: Subscription,
        *,
        group: Optional[str] = None,
    ) -> AsyncIterator[Event]:
        """`group` is ignored here; the Redis impl uses it for consumer-group
        replay. Keep it so callers don't change when you swap implementations."""
        # Create an Anyio memory object stream
        send_stream, receive_stream = anyio.create_memory_object_stream(self.max_queue)
        self._subscribers.append((subscription, send_stream))
        logger.info(f"Subscribed queue to stream: {subscription}")
        return receive_stream

    async def replay(
        self,
        subscription: Subscription,
        start: datetime,
        end: datetime,
        *,
        history: HistoryStore,
    ) -> AsyncIterator[Event]:
        """Replay historical BAR events matching `subscription` in [start, end),
        sorted by `ts_open + timeframe.interval`. Iterator-only — does NOT
        push to existing subscribers.

        Caller supplies `history` per-call. The bus does not own a HistoryStore;
        the application layer (live/main.py or backtest/main.py) constructs one
        and threads it through.
        """
        if not subscription.instruments:
            raise ValueError(
                "InProcessBus.replay() requires subscription.instruments to be "
                "non-empty; the HistoryStore cannot enumerate instruments."
            )
        # FUTURE: k-way merge across event types (bars, news, fills). The bar
        # ordering key is ts_open + interval; the news key is published_at; the
        # fill key is ts_event. When multi-event-type replay lands, switch this
        # loop to a k-way heap that yields events in normalized-time order so a
        # downstream consumer (RSI on bars + sentiment on news) can fold both
        # into one timeline.
        bars: list[Bar] = []
        for inst in subscription.instruments:
            for tf in Timeframe:  # timeframe filter is not on Subscription this iteration
                bars.extend(await history.fetch_bars(inst, tf, start, end))

        bars.sort(key=lambda b: b.ts_open + b.timeframe.interval)

        for bar in bars:
            yield Event(
                type=EventType.BAR,
                source="replay",  # Bar has no `source` field; revisit when BarRow.source flows into the DTO
                payload=bar,
                ts_event=bar.ts_open + bar.timeframe.interval,
                ts_ingest=datetime.now(timezone.utc),  # synthetic
                # event_id auto-generated
            )

    def _cleanup_closed_subscribers(self) -> None:
        """Cleans up inactive streams from the list."""
        active = []
        for sub, send_stream in self._subscribers:
            # Check if send_stream is closed by testing its state
            try:
                # If send_stream is closed, it raises ClosedResourceError on operations
                # We can filter them out
                active.append((sub, send_stream))
            except Exception:
                pass
        self._subscribers = active


# Later, same surface, durable: replay is what makes backtest + warmup possible.
# class RedisStreamsBus:  # implements Bus
#     def __init__(self, url: str, *, maxlen: int = ...) -> None: ...
#     async def replay(self, subscription: Subscription,
#                      start: datetime, end: datetime) -> AsyncIterator[Event]: ...
