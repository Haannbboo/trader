import anyio
import time
from typing import Callable, Awaitable, Any
from loguru import logger


class WebSocketConnection:
    """Manages WebSocket connection lifecycle, including exponential backoff reconnects."""

    def __init__(
        self,
        url: str,
        handler: Callable[[str], Awaitable[None]],
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        backoff_factor: float = 2.0,
    ) -> None:
        self.url = url
        self.handler = handler
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_factor = backoff_factor
        self._running = False

    async def connect_and_listen(self, mock_ws_client: Any = None) -> None:
        """Runs the receive loop and handles automatic reconnection."""
        self._running = True
        backoff = self.initial_backoff

        while self._running:
            try:
                logger.info(f"Connecting to WebSocket: {self.url}")
                # Mock actual connection setup. In a real system, we'd do:
                # async with websockets.connect(self.url) as ws:
                #     backoff = self.initial_backoff
                #     while self._running:
                #         msg = await ws.recv()
                #         await self.handler(msg)

                # For simulation, run a mock ws client or simulate message cycle
                if mock_ws_client:
                    await mock_ws_client.start_listening(self.handler)
                else:
                    # Generic simulated idle connection
                    backoff = self.initial_backoff
                    while self._running:
                        await anyio.sleep(1.0)

            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"WebSocket disconnected from {self.url} ({e}). Reconnecting in {backoff}s...")
                await anyio.sleep(backoff)
                backoff = min(backoff * self.backoff_factor, self.max_backoff)

    def stop(self) -> None:
        self._running = False
        logger.info(f"Stopped WebSocket listener for {self.url}")


class RateLimiter:
    """Simple rate limiter implementing the token bucket algorithm for API requests."""

    def __init__(self, rate_limit: int, period: float = 1.0) -> None:
        self.rate_limit = rate_limit
        self.period = period
        self.tokens = float(rate_limit)
        self.last_update = time.monotonic()

    async def acquire(self) -> None:
        """Acquires a single token, blocking if rate limit is reached."""
        while True:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now

            # Replenish tokens based on time elapsed
            self.tokens = min(
                self.rate_limit,
                self.tokens + elapsed * (self.rate_limit / self.period)
            )

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            else:
                # Sleep a tiny bit before trying again
                sleep_dur = (1.0 - self.tokens) * (self.period / self.rate_limit)
                await anyio.sleep(max(0.001, sleep_dur))


class Poller:
    """Invokes a task periodically using a configurable polling interval."""

    def __init__(self, interval_seconds: float, task: Callable[[], Awaitable[None]]) -> None:
        self.interval = interval_seconds
        self.task = task
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info(f"Starting poller task with interval: {self.interval}s")
        while self._running:
            try:
                await self.task()
            except Exception as e:
                logger.error(f"Error in poller task execution: {e}")
            await anyio.sleep(self.interval)

    def stop(self) -> None:
        self._running = False
        logger.info("Stopped poller task")
