"""persistence.repository — the READ face of persistence.

Implements the HistoryStore port (defined in contracts.ports). Two consumers:

  1. domain services' HISTORICAL read path: MarketDataService.get_bars(start,end)
     for a past range routes here (live ranges route to the adapter/cache). The
     agent/frontend call get_bars and never know the source — service decides by
     time range. So historical reads need NO new service, just this backing.
  2. (future) replay harnesses, dashboards, and any consumer that needs to
     iterate stored facts in time order.

Returns schema DTOs (Bar / NewsItem / Fill), not ORM rows — callers stay in the
schema vocabulary, the ORM never leaks past this boundary.

replay_events (k-way merge across bars+news+fills in ts_event global order) is
intentionally out of scope for this iteration; see ADR-0003 and the
"Follow-ups" list in the design spec.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, overload

from contracts.schema import Bar, Fill, Instrument, NewsItem, OptionRight, Timeframe
from loguru import logger
from sqlalchemy import select

from persistence.engine import Database
from persistence.models import BarRow, FillRow, NewsRow


@overload
def _utc(dt: datetime) -> datetime: ...


@overload
def _utc(dt: Optional[datetime]) -> Optional[datetime]: ...


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Re-attach UTC tzinfo if a backend (SQLite) stripped it on roundtrip.

    The schema contract says "All timestamps are tz-aware UTC." Postgres'
    `timestamptz` keeps tzinfo; SQLite's DateTime drops it. Both backends store
    timestamps already-normalized to UTC, so re-attaching here is lossless.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Repository:
    """Read-only access to stored raw facts. Implements HistoryStore.

    All methods return schema DTOs (frozen pydantic), never ORM rows.
    Empty result sets return []. A start > end range is treated as a
    no-op query, returning [] and logging a warning — it's a contract
    issue from the caller, not an error to propagate.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # bars
    # ------------------------------------------------------------------
    async def fetch_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Return bars for (instrument, timeframe) in [start, end] ts_open order.

        The Instrument is flattened to its canonical key for the query
        (instrument_key already uniquely identifies an option contract, so the
        PK needs nothing more). The mapper re-inflates the flattened columns
        back into a schema.Instrument on the way out.
        """
        if start > end:
            logger.warning(
                "fetch_bars: start {} > end {}; returning empty list",
                start,
                end,
            )
            return []

        async with self._db.session() as s:
            stmt = (
                select(BarRow)
                .where(
                    BarRow.instrument_key == instrument.key,
                    BarRow.timeframe == timeframe.value,
                    BarRow.ts_open >= start,
                    BarRow.ts_open <= end,
                )
                .order_by(BarRow.ts_open)
            )
            rows = (await s.execute(stmt)).scalars().all()

        return [_bar_row_to_dto(r) for r in rows]

    # ------------------------------------------------------------------
    # news
    # ------------------------------------------------------------------
    async def fetch_news(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[NewsItem]:
        """Return news in [start, end] published_at order.

        NOTE: the instruments=... filter is intentionally NOT in this signature
        yet. The NewsRow model has no instrument column; adding the filter here
        now would force a silent ignore or a NotImplementedError. Adding a
        news_instruments link table is a follow-up — see ADR-0003.
        """
        if start is not None and end is not None and start > end:
            logger.warning(
                "fetch_news: start {} > end {}; returning empty list",
                start,
                end,
            )
            return []

        async with self._db.session() as s:
            stmt = select(NewsRow)
            if start is not None:
                stmt = stmt.where(NewsRow.published_at >= start)
            if end is not None:
                stmt = stmt.where(NewsRow.published_at <= end)
            stmt = stmt.order_by(NewsRow.published_at)
            rows = (await s.execute(stmt)).scalars().all()

        return [_news_row_to_dto(r) for r in rows]

    # ------------------------------------------------------------------
    # fills
    # ------------------------------------------------------------------
    async def fetch_fills(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        broker_order_id: Optional[str] = None,
    ) -> list[Fill]:
        """Return fills in [start, end] ts_event order.

        Optional broker_order_id narrows to a single order. The
        client_order_id=... filter is deferred (FillRow doesn't carry
        client_order_id) — see ADR-0003.
        """
        if start is not None and end is not None and start > end:
            logger.warning(
                "fetch_fills: start {} > end {}; returning empty list",
                start,
                end,
            )
            return []

        async with self._db.session() as s:
            stmt = select(FillRow)
            if start is not None:
                stmt = stmt.where(FillRow.ts_event >= start)
            if end is not None:
                stmt = stmt.where(FillRow.ts_event <= end)
            if broker_order_id is not None:
                stmt = stmt.where(FillRow.broker_order_id == broker_order_id)
            stmt = stmt.order_by(FillRow.ts_event)
            rows = (await s.execute(stmt)).scalars().all()

        return [_fill_row_to_dto(r) for r in rows]


# ----------------------------------------------------------------------
# Row -> DTO mappers (pure functions; no DB, no Session).
# ----------------------------------------------------------------------
def _instrument_from_row(row) -> Instrument:
    """Re-inflate the flattened _InstrumentCols back into a schema.Instrument.

    For an equity row, right/expiry/strike are None — the Instrument
    constructor's defaults match. For an option row, right is the lowercase
    OptionRight.value string ("call"/"put") stored by the writer.

    KNOWN LIMITATION: schema.Instrument has `currency` and `exchange` fields
    that are NOT in _InstrumentCols — they are not persisted. The re-inflated
    Instrument always has currency="USD" (the Instrument default) and
    exchange=None. Fixing this is a model change (add columns, migrate,
    update writer) — out of scope for this PR; see ADR-0003.
    """
    right = OptionRight(row.right) if row.right is not None else None
    return Instrument(
        symbol=row.symbol,
        asset_class=row.asset_class,  # str -> AssetClass via pydantic coercion
        expiry=_utc(row.expiry),
        strike=row.strike,
        right=right,
        multiplier=row.multiplier,
    )


def _bar_row_to_dto(row: BarRow) -> Bar:
    return Bar(
        instrument=_instrument_from_row(row),
        timeframe=Timeframe(row.timeframe),
        ts_open=_utc(row.ts_open),
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
    )


def _news_row_to_dto(row: NewsRow) -> NewsItem:
    return NewsItem(
        id=row.id,
        source=row.source,
        published_at=_utc(row.published_at),
        headline=row.headline,
        body=row.body,
        url=row.url,
    )


def _fill_row_to_dto(row: FillRow) -> Fill:
    from contracts.schema import Fill, Side

    return Fill(
        fill_id=row.fill_id,
        broker_order_id=row.broker_order_id,
        instrument=_instrument_from_row(row),
        side=Side(row.side),
        quantity=row.quantity,
        price=row.price,
        ts_event=_utc(row.ts_event),
        fee=row.fee,
    )
