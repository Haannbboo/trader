from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from contracts import (
    Bar,
    Bus,
    Event,
    Instrument,
    MarketChannel,
    MarketDataService,
    MarketSourcePort,
    Quote,
    Timeframe,
)


class MarketService(MarketDataService):
    """Aggregates market data adapters, manages subscription reuse, and deduplicates feeds."""

    def __init__(self, sources: list[MarketSourcePort], bus: Bus) -> None:
        """Initialize MarketService with market sources and event bus."""
        self.sources = sources
        self.bus = bus

    async def start(self) -> None:
        """Start the market data service and connect all sources."""
        raise NotImplementedError()

    async def stop(self) -> None:
        """Stop the market data service and disconnect all sources."""
        raise NotImplementedError()

    async def get_quote(self, instrument: Instrument) -> Quote:
        """Fetch the latest quote for a given instrument."""
        raise NotImplementedError()

    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Fetch historical bars for a given instrument and timeframe."""
        raise NotImplementedError()

    def subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]:
        """Subscribe to real-time streams (quotes, trades, bars) for instruments."""
        raise NotImplementedError()

    def _route(self, instrument: Instrument) -> MarketSourcePort:
        """Pick a source by declared capabilities (+ failover order), not by name."""
        raise NotImplementedError()

    def _multiplex(self, instrument: Instrument) -> AsyncIterator[Event]:
        """One upstream subscription per instrument, fanned out to all callers."""
        raise NotImplementedError()
