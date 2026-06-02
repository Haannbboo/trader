import anyio
from typing import Set, List, Callable, Awaitable
from loguru import logger
from transport import RateLimiter
from contracts import SourceCapabilities, SourceMode, AssetClass


class BaseAdapter:
    """Base class for adapters, incorporating shared lifecycle, reconnect, and rate-limiting."""

    def __init__(self, name: str, rate_limit: int = 10) -> None:
        self.name = name
        self.connected = False
        self.rate_limiter = RateLimiter(rate_limit=rate_limit, period=1.0)
        self.active_subscriptions: Set[str] = set()
        self._capabilities = SourceCapabilities(
            mode=SourceMode.PUSH,
            supports_streaming=True,
            asset_classes=(AssetClass.EQUITY,)
        )

    @property
    def capabilities(self) -> SourceCapabilities:
        return self._capabilities

    async def start(self) -> None:
        await self.connect()

    async def stop(self) -> None:
        await self.disconnect()

    async def health(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        """Sets connection state to True. Adapters should override and call super()."""
        await self.rate_limiter.acquire()
        self.connected = True
        logger.info(f"[{self.name}] Connected successfully.")

    async def disconnect(self) -> None:
        """Sets connection state to False."""
        self.connected = False
        logger.info(f"[{self.name}] Disconnected.")

    def track_subscription(self, symbol: str) -> bool:
        """Helper to check and track sub subscriptions, returns True if it's a new subscription."""
        if symbol in self.active_subscriptions:
            logger.debug(f"[{self.name}] Already subscribed to '{symbol}', skipping duplicate request.")
            return False
        self.active_subscriptions.add(symbol)
        return True
