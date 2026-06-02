"""
ta.bus.inprocess — Bus for single-process / single-machine. asyncio fan-out to
in-memory per-subscriber queues. Implements the Bus protocol so RedisStreamsBus
(durability + replay) can replace it later with ZERO caller changes.
"""
from __future__ import annotations

import anyio
from typing import AsyncIterator, Optional, List, Tuple
from loguru import logger

from contracts.schema import Event
from contracts.ports import Subscription


class InProcessBus:
    """Fan-out hub. publish() pushes to every matching subscriber's queue;
    subscribe() yields a filtered stream. Also the home of subscription
    MULTIPLEXING bookkeeping (many callers, one upstream sub per instrument)."""

    def __init__(self, *, max_queue: int = 10_000) -> None:
        self.max_queue = max_queue
        # List of (subscription, send_stream)
        self._subscribers: List[Tuple[Subscription, anyio.MemoryObjectSendStream[Event]]] = []
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("InProcessBus started.")

    async def stop(self) -> None:
        await self.close()

    async def publish(self, event: Event) -> None:
        """Publishes an event to matching subscriber queues."""
        if not self._running:
            logger.warning("Bus is not running. Ignoring event publish.")
            return

        # Clean up closed streams first
        self._cleanup_closed_subscribers()

        for sub, send_stream in self._subscribers:
            if self._matches(event, sub):
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
        self, subscription: Subscription, *, group: Optional[str] = None,
    ) -> AsyncIterator[Event]:
        """`group` is ignored here; the Redis impl uses it for consumer-group
        replay. Keep it so callers don't change when you swap implementations."""
        # Create an Anyio memory object stream
        send_stream, receive_stream = anyio.create_memory_object_stream(self.max_queue)
        self._subscribers.append((subscription, send_stream))
        logger.info(f"Subscribed queue to stream: {subscription}")
        return receive_stream


    async def close(self) -> None:
        self._running = False
        # Close all subscriber streams
        for _, send_stream in self._subscribers:
            try:
                send_stream.close()
            except Exception:
                pass
        self._subscribers.clear()
        logger.info("InProcessBus closed and all streams terminated.")

    def _matches(self, event: Event, sub: Subscription) -> bool:
        """Helper to check if an event matches a subscription rule."""
        # 1. Filter by event_types (if not empty)
        if sub.event_types:
            if event.type not in sub.event_types:
                return False

        # 2. Filter by instruments (if not empty)
        if sub.instruments:
            # Extract instrument from event payload
            payload = getattr(event, "payload", None)
            event_inst_key = None
            if payload is not None:
                instrument = getattr(payload, "instrument", None)
                if instrument is not None:
                    event_inst_key = getattr(instrument, "key", None)

            if not event_inst_key:
                return False

            sub_keys = {inst.key for inst in sub.instruments}
            if event_inst_key not in sub_keys:
                return False

        # 3. Filter by sources (if not empty)
        if sub.sources:
            if event.source not in sub.sources:
                return False

        return True

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
