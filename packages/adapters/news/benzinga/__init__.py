from typing import AsyncIterator
from contracts import NewsFilter, NewsItem, Event
from plugins import register
from adapters._base import BaseNewsAdapter


@register("news", "benzinga")
class BenzingaNewsAdapter(BaseNewsAdapter):
    """Benzinga News Feed Adapter (Skeleton)."""

    def __init__(self) -> None:
        super().__init__(name="BenzingaNewsAdapter", rate_limit=10)

    async def query(self, flt: NewsFilter) -> list[NewsItem]:
        return []

    async def subscribe(self, flt: NewsFilter) -> AsyncIterator[Event]:
        if False:
            yield
