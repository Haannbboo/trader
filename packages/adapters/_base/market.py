"""
adapters/_base/market.py — BaseMarketAdapter: shared by MARKET sources only.

Owns the market pipeline (rate-limit -> fetch/recv -> normalize hook -> wrap in
Event -> emit). A concrete market adapter fills only: _connect (in BaseAdapter),
the two _normalize_* hooks, and the source-specific subscribe wiring.

Structurally satisfies MarketSourcePort. Depends on contracts + transport + base.
"""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Set
from loguru import logger
from contracts import (
    Bar,
    Event,
    EventType,
    Instrument,
    MarketChannel,
    MarketSourcePort,
    Quote,
    Timeframe,
)
from adapters._base.base import BaseAdapter


class BaseMarketAdapter(BaseAdapter, MarketSourcePort):
    """Base class for market data adapters."""

    def __init__(self, name: str = "", rate_limit: int = 10, **params) -> None:
        super().__init__(name=name, rate_limit=rate_limit, **params)
        self.active_subscriptions: Set[str] = set()

    def track_subscription(self, symbol: str) -> bool:
        """Helper to check and track subscriptions, returns True if it's a new subscription."""
        if symbol in self.active_subscriptions:
            logger.debug(
                f"[{self.name}] Already subscribed to '{symbol}', skipping duplicate request."
            )
            return False
        self.active_subscriptions.add(symbol)
        return True

    # --- MarketSourcePort surface: common flow here ---
    async def get_quote(self, instrument: Instrument) -> Quote:
        """Common: acquire limiter -> _fetch_quote_raw -> _normalize_quote."""
        raise NotImplementedError()

    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Common: acquire limiter -> _fetch_bars_raw -> map _normalize_bar."""
        raise NotImplementedError()

    async def subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]:
        """Common: translate channels via _channel_map, open the upstream stream
        (PUSH adapters yield raw frames; POLL adapters drive a Poller), then run
        each raw item through the right _normalize_* and wrap as Event[Quote|Bar].
        Whether it's push or poll dies here — the caller just gets Events."""
        if False:
            yield
        raise NotImplementedError()

    # --- hooks a concrete market source fills ---
    def _channel_map(self, channels: list[MarketChannel]) -> list[str]:
        """Map our channels to the source's native subscription tokens."""
        raise NotImplementedError()

    async def _fetch_quote_raw(self, instrument: Instrument) -> dict:
        """Fetch raw quote dictionary from the market source API."""
        raise NotImplementedError()

    async def _fetch_bars_raw(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Fetch raw list of bar dictionaries from the market source API."""
        raise NotImplementedError()

    def _normalize_quote(self, raw: dict) -> Quote:
        """Raw source payload -> schema.Quote. The main place market adapters
        differ; pydantic validation catches mistakes here."""
        raise NotImplementedError()

    def _normalize_bar(self, raw: dict) -> Bar:
        """Raw source payload -> schema.Bar."""
        raise NotImplementedError()

    def _wrap(self, payload, event_type: EventType) -> Event:
        """Common: stamp source/ts and box a normalized DTO into an Event."""
        raise NotImplementedError()
