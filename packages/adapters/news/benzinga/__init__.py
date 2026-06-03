from typing import AsyncIterator

from adapters._base import BaseNewsAdapter
from contracts import Event, NewsFilter, NewsItem
from plugins import register


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
