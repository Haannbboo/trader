"""
persistence.models — declarative table definitions for the time-series store.

We persist ONLY raw facts, never derived values:
  - bars   : market OHLCV  (replay, factor backtest, frontend charts)
  - news   : news items     (replay, frontend, agent, sentiment backtest)
  - fills  : executions     (P&L attribution, restart recovery, audit)
Quotes (ticks) and factor values are NOT stored by default — ticks are huge and
factors are recomputable from raw facts (storing them creates a drifting copy).

This is TimescaleDB = Postgres + extension, so your SQLAlchemy / declarative
knowledge transfers 1:1. The only Timescale-specific step is turning these into
hypertables (a one-time `SELECT create_hypertable('bars','ts_open')` in a
migration); SQLAlchemy queries against them are ordinary SQL.

----------------------------------------------------------------------------
RULE — persisting an Instrument means flattening its OPTION fields too.
----------------------------------------------------------------------------
schema.Instrument is polymorphic: equity uses {symbol, asset_class}, but option/
future ALSO carry {expiry, strike, right, multiplier}. ANY table that stores an
Instrument MUST flatten those derivative columns (nullable for equity), or option
rows are CORRUPT — you can't tell SPX 5000P 2026-06-01 from SPX 4900C, and you
can't filter by strike / expiry / right.

Two distinct purposes, don't conflate them:
  - instrument_key (the canonical "SPX|20260601|P|5000" string) -> UNIQUENESS
    (part of the PK; already fully identifies an option contract).
  - flattened symbol/strike/expiry/right columns -> QUERYABILITY (WHERE / ORDER
    BY / GROUP BY by strike, expiry, etc. — which option analysis needs heavily).
A field that will ever be filtered/sorted/grouped must be its own column, not
buried in the key string.

This rule applies to every future Instrument-bearing table too (a QuoteRow if you
ever store ticks, a PositionRow for snapshots): flatten the option fields there as
well. Decided once here; see docs/adr.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class _InstrumentCols:
    """Mixin: the flattened Instrument columns shared by every fact table.
    Equity leaves the option columns NULL; option/future fill them in."""

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[str] = mapped_column(String(16), index=True)
    instrument_key: Mapped[str] = mapped_column(String(128), index=True)
    # --- option / derivative fields (NULL for equity) ---
    expiry: Mapped["datetime | None"] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    strike: Mapped["Decimal | None"] = mapped_column(Numeric, nullable=True, index=True)
    right: Mapped["str | None"] = mapped_column(
        String(4), nullable=True, index=True
    )  # "call"/"put"
    multiplier: Mapped[Decimal] = mapped_column(Numeric, default=1)


class BarRow(_InstrumentCols, Base):
    __tablename__ = "bars"
    # PK: one bar per (contract, timeframe, bar-open). instrument_key already
    # uniquely identifies the option contract, so the PK needs nothing more.
    instrument_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts_open: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric)
    high: Mapped[Decimal] = mapped_column(Numeric)
    low: Mapped[Decimal] = mapped_column(Numeric)
    close: Mapped[Decimal] = mapped_column(Numeric)
    volume: Mapped[Decimal] = mapped_column(Numeric)
    source: Mapped[str] = mapped_column(String(32))


class NewsRow(Base):
    __tablename__ = "news"
    # News isn't bound to a specific contract (at most an underlying), so it does
    # NOT carry option columns. Instruments mentioned -> a news_instruments link
    # table (cleanest for querying "news about NVDA"); left as a design choice.
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    headline: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)


class FillRow(_InstrumentCols, Base):
    __tablename__ = "fills"
    # Option fills carry strike/expiry/right too (via the mixin) — needed for
    # per-contract P&L. instrument_key here is a plain indexed column, not PK.
    fill_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker_order_id: Mapped[str] = mapped_column(String(64), index=True)
    ts_event: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Numeric)
    price: Mapped[Decimal] = mapped_column(Numeric)
    fee: Mapped[Decimal] = mapped_column(Numeric)
    source: Mapped[str] = mapped_column(String(32))
