"""
adapters/_base/base.py — BaseAdapter: shared by EVERY source (market/news/account).

Holds ONLY what is independent of *what the data is*: lifecycle, rate limiting,
error wrapping. It does not know Quote / Position / NewsItem — that's the whole
reason all three domains can share it. Depends on contracts + transport.

Template-method split used throughout this file:
  - public methods (start/stop/health) own the COMMON flow;
  - `_`-prefixed hooks are the source-specific bits a concrete adapter fills in.
"""
from __future__ import annotations

import anyio
from typing import Any
from loguru import logger
from transport import RateLimiter
from contracts import SourceCapabilities, SourceMode, AssetClass
from contracts.errors import TraderError


class BaseAdapter:
    """Base class for adapters, incorporating shared lifecycle, reconnect, and rate-limiting.

    This class holds only what is independent of what the data is: lifecycle,
    rate limiting, and error wrapping.
    """

    name: str

    def __init__(self, name: str = "", rate_limit: int = 10, **params) -> None:
        """Stash params, and initialize the RateLimiter and state variables."""
        self.name = name
        self.connected = False
        self._started = False
        self.params = params
        self.rate_limiter = RateLimiter(rate_limit=rate_limit, period=1.0)
        self._capabilities = SourceCapabilities(
            mode=SourceMode.PUSH,
            supports_streaming=True,
            asset_classes=(AssetClass.EQUITY,)
        )

    @property
    def capabilities(self) -> SourceCapabilities:
        """MUST be provided by the concrete adapter."""
        return self._capabilities

    @property
    def limiter(self) -> RateLimiter:
        """Shared token bucket; adapters await self.limiter.acquire() before each upstream call."""
        return self.rate_limiter

    # --- lifecycle: common flow here, source-specific connect in the hooks ---
    async def start(self) -> None:
        """Common: guard double-start, then call _connect(), mark healthy."""
        if self._started:
            logger.warning(f"[{self.name}] Already started.")
            return

        logger.info(f"[{self.name}] Starting adapter...")
        try:
            await self._connect()
            self._started = True
            self.connected = True
            logger.info(f"[{self.name}] Started and connected successfully.")
        except Exception as e:
            wrapped = self._wrap_error(e)
            logger.error(f"[{self.name}] Failed to start: {wrapped}")
            raise wrapped

    async def stop(self) -> None:
        """Common: call _disconnect(), release resources, mark stopped."""
        if not self._started:
            logger.warning(f"[{self.name}] Already stopped or not started.")
            return

        logger.info(f"[{self.name}] Stopping adapter...")
        try:
            await self._disconnect()
        except Exception as e:
            wrapped = self._wrap_error(e)
            logger.error(f"[{self.name}] Error during disconnect: {wrapped}")
        finally:
            self._started = False
            self.connected = False
            logger.info(f"[{self.name}] Stopped successfully.")

    async def health(self) -> bool:
        """Common: default liveness; a source may refine via _check_health()."""
        if not self._started:
            return False
        try:
            return await self._check_health()
        except Exception as e:
            logger.warning(f"[{self.name}] Health check failed: {e}")
            return False

    def _wrap_error(self, exc: Exception) -> Exception:
        """Common: translate a raw upstream exception into a typed contracts.errors.*."""
        if isinstance(exc, TraderError):
            return exc
        return TraderError(str(exc))

    # --- hooks every source fills (no default behavior) ---
    async def _connect(self) -> None:
        """Hook for concrete adapter to establish connection."""
        pass

    async def _disconnect(self) -> None:
        """Hook for concrete adapter to close connection/release resources."""
        pass

    async def _check_health(self) -> bool:
        """Hook for concrete adapter to refine liveness check."""
        return self.connected
