"""
ta.transport — reusable transport plumbing so every adapter does NOT reimplement
reconnect / rate-limit / poll loops. Knows nothing about normalization or any
specific source. Depends only on stdlib/contracts.
"""

from __future__ import annotations

from typing import AsyncIterator, Awaitable, Callable, TypeVar

T = TypeVar("T")


class RateLimiter:
    """Token bucket. Adapters await acquire() before each upstream call."""

    def __init__(self, rate_per_sec: float, burst: int = 1) -> None: ...
    async def acquire(self, tokens: int = 1) -> None: ...


class ReconnectingWebsocket:
    """Owns one upstream WS connection: connect, heartbeat, exponential-backoff
    reconnect. messages() yields raw frames and transparently reconnects, so a
    push adapter never writes reconnect logic itself."""

    def __init__(
        self,
        url: str,
        *,
        heartbeat_s: float = 15.0,
        max_backoff_s: float = 30.0,
    ) -> None: ...
    async def connect(self) -> None: ...
    async def send(self, message: str | bytes) -> None: ...
    def messages(self) -> AsyncIterator[bytes]: ...
    async def close(self) -> None: ...


class Poller:
    """Drives a poll-based source on a fixed cadence with jitter + error backoff,
    turning periodic pulls into a stream. This is HOW a POLL source satisfies the
    same subscribe()->AsyncIterator[Event] contract as a PUSH source."""

    def __init__(self, interval_s: float, *, jitter: float = 0.1) -> None: ...
    def run(self, fetch: Callable[[], Awaitable[list[T]]]) -> AsyncIterator[T]: ...
