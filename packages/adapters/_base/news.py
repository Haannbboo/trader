"""
adapters/_base/news.py — BaseNewsAdapter: shared by NEWS sources only.

Owns the news pipeline (filter -> fetch/recv -> normalize hook -> dedup id ->
Event). A concrete news source fills: NewsFilter -> its query params, the
_normalize_item hook, and how to extract a stable dedup id. (System-wide
cross-source dedup is NewsService's job; this base only normalizes one source.)

Structurally satisfies NewsSourcePort. Depends on contracts + transport + base.
"""

from __future__ import annotations

from typing import AsyncIterator

from adapters._base.base import BaseAdapter
from contracts import Event, NewsFilter, NewsItem, NewsSourcePort


class BaseNewsAdapter(BaseAdapter, NewsSourcePort):
    """Base class for news adapters."""

    async def query(self, flt: NewsFilter) -> list[NewsItem]:
        """Common: limiter -> _build_query(flt) -> _fetch_raw -> map _normalize_item."""
        raise NotImplementedError()

    async def subscribe(self, flt: NewsFilter) -> AsyncIterator[Event]:
        """Common: open upstream stream (push or polled), normalize each item,
        wrap as Event[NewsItem]."""
        if False:
            yield
        raise NotImplementedError()

    # --- hooks a concrete news source fills ---
    def _build_query(self, flt: NewsFilter) -> dict:
        """NewsFilter -> the source's native query parameters."""
        raise NotImplementedError()

    async def _fetch_raw(self, params: dict) -> list[dict]:
        """Fetch raw news data from the source API."""
        raise NotImplementedError()

    def _normalize_item(self, raw: dict) -> NewsItem:
        """Normalize raw news JSON into a NewsItem contract."""
        raise NotImplementedError()

    def _dedup_id(self, raw: dict) -> str:
        """Stable per-source id used to drop intra-source repeats (and fed to
        NewsService for cross-source collapsing)."""
        raise NotImplementedError()
