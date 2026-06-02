from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from adapters._base import BaseMarketAdapter
from contracts.ports import MarketChannel, SourceCapabilities
from contracts.schema import Bar, Event, Instrument, Quote, Timeframe
from plugins import register


@register("market", "ibkr")
class IBKRMarketAdapter(BaseMarketAdapter):
    name = "ibkr"

    def __init__(self) -> None:
        super().__init__(name="IBKRMarketAdapter", rate_limit=50)

    @property
    def capabilities(self) -> SourceCapabilities:
        return self._capabilities

    # --- MarketSourcePort ---
    async def get_quote(self, instrument: Instrument) -> Quote:
        raise NotImplementedError()

    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        return []

    async def subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]:
        if False:
            yield

    # --- source-specific normalization (the only real work) ---
    def _normalize_quote(self, raw: dict) -> Quote:
        raise NotImplementedError()

    def _normalize_bar(self, raw: dict) -> Bar:
        raise NotImplementedError()
