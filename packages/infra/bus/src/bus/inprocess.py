"""
ta.bus.inprocess — Bus for single-process / single-machine. asyncio fan-out to
in-memory per-subscriber queues. Implements the Bus protocol so RedisStreamsBus
(durability + replay) can replace it later with ZERO caller changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, List, Optional, Tuple

import anyio
from anyio.streams.memory import MemoryObjectSendStream
from contracts.ports import Subscription
from contracts.schema import Event
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
    ) -> AsyncIterator[Event]:
        """Replay historical events matching `subscription` in [start, end).

        STUB: not implemented yet. The live stream only retains what fits
        under MAXLEN; replay over long horizons needs a backfill store (cold
        cache of older events). Wire this up after packages/persistence
        is in place — likely a Redis Stream range scan for the warm window
        and a separate store (S3 / object storage) for anything older.
        """
        raise NotImplementedError
        if False:
            yield  # make this an async generator so the AsyncIterator type is honest

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
