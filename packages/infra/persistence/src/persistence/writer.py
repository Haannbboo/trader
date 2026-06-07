"""
persistence.writer — the WRITE face of persistence: a bus consumer that
durably stores the raw-fact events. It is the same KIND of thing as a feature
worker or smoke's bus_watcher — subscribe the bus, do something per event — but
here "do something" is "insert a row".

Crucial: it subscribes ONLY to the EventTypes worth storing (BAR / NEWS / FILL)
and ignores everything else, so QUOTE ticks and FEATURE values keep flowing on
the bus for live consumers without ever touching the DB. WHAT to persist is the
design decision; this class is just the mechanism.

Wiring: apps/live starts run() as one more task in the asyncio.gather alongside
service.start() and gateway.serve(). It shares the same bus + loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from contracts.ports import Bus, Subscription
from contracts.schema import Bar, Event, EventType, Fill, NewsItem
from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from persistence.engine import Database
from persistence.models import BarRow, FillRow, NewsRow

# only these are persisted; everything else stays bus-only
_PERSISTED = (EventType.BAR, EventType.NEWS, EventType.FILL)


class DbWriter:
    """Pure database write operations, decoupled from event bus subscription logic."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def store_bars(self, bars: list[Bar], source: str) -> None:
        """Upsert a list of Bar DTOs to the database."""
        async with self._db.session() as session:
            for bar in bars:
                row = self.bar_row(bar, source)
                await self.upsert(session, row)

    async def upsert(self, session, row) -> None:
        """INSERT the row, ignoring conflicts on the natural PK.

        Dialect switch via Database.dialect_name:
          - "postgresql" -> ON CONFLICT DO NOTHING (Postgres 9.5+)
          - "sqlite"     -> INSERT OR IGNORE
        Index elements are read from the table's primary key so the upsert
        stays in sync with the schema.
        """
        row_type = type(row)
        values = {c.name: getattr(row, c.name) for c in row_type.__table__.columns}

        # pg_insert and sqlite_insert return different Insert subclasses; Session.execute
        # accepts either, so we type the local as Any to keep the dialect switch readable.
        stmt: Any
        if self._db.dialect_name == "postgresql":
            stmt = (
                pg_insert(row_type)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=list(row_type.__table__.primary_key.columns)
                )
            )
        else:  # sqlite (dev/test)
            stmt = sqlite_insert(row_type).values(**values).on_conflict_do_nothing()

        await session.execute(stmt)

    # --- Event payload (schema DTO) -> ORM row ---
    @staticmethod
    def _instrument_cols(inst) -> dict:
        """Flatten an Instrument into the shared columns. Option fields are None
        for equity; this is the single place the flatten rule is applied, so a
        new Instrument-bearing table just reuses it (and can't forget a field)."""
        return dict(
            symbol=inst.symbol,
            asset_class=inst.asset_class.value,
            instrument_key=inst.key,
            expiry=inst.expiry,
            strike=inst.strike,
            right=inst.right.value if inst.right is not None else None,
            multiplier=inst.multiplier,
        )

    def bar_row(self, bar: Bar, source: str) -> BarRow:
        return BarRow(
            timeframe=bar.timeframe.value,
            ts_open=bar.ts_open,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            source=source,
            **self._instrument_cols(bar.instrument),
        )

    def news_row(self, n: NewsItem) -> NewsRow:
        return NewsRow(
            id=n.id,
            source=n.source,
            published_at=n.published_at,
            headline=n.headline,
            body=n.body,
            url=n.url,
        )

    def fill_row(self, f: Fill, source: str) -> FillRow:
        return FillRow(
            fill_id=f.fill_id,
            broker_order_id=f.broker_order_id,
            ts_event=f.ts_event,
            side=f.side.value,
            quantity=f.quantity,
            price=f.price,
            fee=f.fee,
            source=source,
            **self._instrument_cols(f.instrument),
        )


class PersistenceWriter:
    """Bus consumer that durably stores BAR / NEWS / FILL events."""

    def __init__(self, bus: Bus, db: Database, *, batch_size: int = 100) -> None:
        """batch_size: consider buffering inserts and flushing in batches — a
        per-event INSERT per bar is too slow at market data rates. Left as a
        knob; v1 may flush every event for simplicity."""
        self._bus = bus
        self._db = db
        self._batch_size = batch_size
        self._writer = DbWriter(db)

    @property
    def writer(self) -> DbWriter:
        """Expose the underlying DbWriter."""
        return self._writer

    async def run(self) -> None:
        """Subscribe to the persisted EventTypes and write each to its table.
        Runs forever (until cancelled on shutdown)."""
        sub = Subscription(event_types=_PERSISTED)
        async for ev in self._bus.subscribe(sub, group="persistence"):
            try:
                await self._handle(ev)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to write event {} to DB", ev)

    async def store_bars(self, bars: list[Bar], source: str) -> None:
        """Upsert a list of Bar DTOs to the database."""
        await self._writer.store_bars(bars, source)

    async def _handle(self, ev: Event) -> None:
        """Route one event to the right row mapper + dialect-aware upsert.

        Idempotent: a re-published event is a no-op, not a duplicate. The PK
        lists come from each row's `__table__.primary_key.columns` so the
        upsert stays correct if the schema changes.
        """
        async with self._db.session() as s:
            if ev.type is EventType.BAR:
                await self._writer.upsert(
                    s, self._writer.bar_row(ev.payload, ev.source)
                )
            elif ev.type is EventType.NEWS:
                await self._writer.upsert(s, self._writer.news_row(ev.payload))
            elif ev.type is EventType.FILL:
                await self._writer.upsert(
                    s, self._writer.fill_row(ev.payload, ev.source)
                )
