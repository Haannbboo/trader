from typing import AsyncIterator, Optional
from contracts import Event, Subscription


class RedisStreamBus:
    """An asynchronous event bus backend using Redis Streams for persistent, multi-process streaming."""

    def __init__(self, redis_url: str) -> None:
        """Initialize RedisStreamBus with a Redis connection URL."""
        self.redis_url = redis_url

    async def start(self) -> None:
        """Start the Redis Streams event bus backend connection."""
        raise NotImplementedError()

    async def stop(self) -> None:
        """Stop the Redis Streams connection."""
        raise NotImplementedError()

    async def publish(self, event: Event) -> None:
        """Publishes an event to a Redis Stream."""
        raise NotImplementedError()

    async def subscribe(
        self,
        subscription: Subscription,
        *,
        group: Optional[str] = None,
    ) -> AsyncIterator[Event]:
        """Registers a listener for a stream topic."""
        raise NotImplementedError()
        if False:
            yield
