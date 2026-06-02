from __future__ import annotations

from typing import AsyncIterator
from contracts import (
    NewsFilter, NewsItem, Event, NewsSourcePort, Bus, NewsService as NewsServiceInterface
)


class NewsService(NewsServiceInterface):
    """Aggregates news feeds and dispatches normalized events onto the message bus."""

    def __init__(self, sources: list[NewsSourcePort], bus: Bus) -> None:
        """Initialize NewsService with news sources and event bus."""
        self.sources = sources
        self.bus = bus

    async def start(self) -> None:
        """Start the news service and connect all news sources."""
        raise NotImplementedError()

    async def stop(self) -> None:
        """Stop the news service and disconnect all news sources."""
        raise NotImplementedError()

    async def query(self, flt: NewsFilter) -> list[NewsItem]:
        """Query historical news items based on filters."""
        raise NotImplementedError()

    def subscribe(self, flt: NewsFilter) -> AsyncIterator[Event]:
        """Subscribe to real-time news events based on filters."""
        raise NotImplementedError()

    def _dedup(self, item: NewsItem) -> bool:
        """Same story breaks on N sources — collapse by (source id / url / hash)."""
        raise NotImplementedError()
