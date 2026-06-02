"""
schema — C1: normalized data contracts for the trading-agent system.

This module is the foundation every other chunk depends on. It contains
*only* data definitions: normalized DTOs and the Event envelope that flows on
the bus. No business logic, no I/O, no imports from other project modules.

If you are an agent implementing another chunk: treat these types as fixed.
Propose changes upstream rather than editing locally — a divergent schema
silently breaks every other module.

Key design decisions (don't undo these without thinking):
- Models are FROZEN (immutable). Events flow through a bus to many consumers;
  immutability means a consumer can never corrupt an event for the others, and
  it makes replay deterministic. "Updating" an order means producing a new copy.
- extra="forbid": an adapter that emits an unexpected field fails loudly during
  normalization instead of silently polluting the stream.
- Money/quantities use Decimal, never float. Factor *outputs* may be float.
- All timestamps are tz-aware UTC. If you ever need sub-microsecond tick
  precision, switch the ts fields to int epoch-nanoseconds HERE and nowhere
  else — no other module should hardcode a time representation.
- RAW FACTS ONLY. Derived values (sentiment, factors) are NOT attached to raw
  DTOs; they travel as FeatureValue payloads produced by the feature layer.
  This is what keeps the derivation layer cleanly separable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Generic, Optional, TypeVar, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class AssetClass(str, Enum):
    EQUITY = "equity"
    OPTION = "option"
    FUTURE = "future"
    INDEX = "index"
    CRYPTO = "crypto"
    FOREX = "forex"


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    PENDING_NEW = "pending_new"
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Timeframe(str, Enum):
    S1 = "1s"
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"


class EventType(str, Enum):
    QUOTE = "quote"
    BAR = "bar"
    NEWS = "news"
    ORDER_UPDATE = "order_update"
    FILL = "fill"
    POSITION_UPDATE = "position_update"
    BALANCE_UPDATE = "balance_update"
    FEATURE = "feature"  # derived value emitted by the feature layer


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Instrument — a plain ticker string is not enough once options/futures exist,
# so the whole system speaks Instrument.
# ---------------------------------------------------------------------------
class Instrument(_Base):
    symbol: str                       # underlying or ticker, e.g. "AAPL", "SPX"
    asset_class: AssetClass
    exchange: Optional[str] = None
    currency: str = "USD"
    # Derivative fields (None for equity/index):
    expiry: Optional[datetime] = None
    strike: Optional[Decimal] = None
    right: Optional[OptionRight] = None
    multiplier: Decimal = Decimal(1)

    @property
    def key(self) -> str:
        """Stable canonical key for bus topics / dedup. Pure formatting only."""
        if self.asset_class is AssetClass.OPTION:
            exp = self.expiry.strftime("%Y%m%d") if self.expiry else "?"
            r = self.right.value[0].upper() if self.right else "?"
            return f"{self.symbol}|{exp}|{r}|{self.strike}"
        return f"{self.symbol}|{self.asset_class.value}"


# ---------------------------------------------------------------------------
# Market DTOs
# ---------------------------------------------------------------------------
class Quote(_Base):
    instrument: Instrument
    ts_event: datetime
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    bid_size: Optional[Decimal] = None
    ask_size: Optional[Decimal] = None
    last: Optional[Decimal] = None
    last_size: Optional[Decimal] = None


class Bar(_Base):
    instrument: Instrument
    timeframe: Timeframe
    ts_open: datetime                 # start of the bar interval
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Optional[Decimal] = None
    trades: Optional[int] = None


# ---------------------------------------------------------------------------
# News DTO — kept RAW. Sentiment is derived and lives in FeatureValue.
# ---------------------------------------------------------------------------
class NewsItem(_Base):
    id: str                           # source-native id, used for dedup
    source: str
    headline: str
    published_at: datetime
    body: Optional[str] = None
    url: Optional[str] = None
    instruments: tuple[Instrument, ...] = ()   # tickers the source tagged
    language: Optional[str] = None


# ---------------------------------------------------------------------------
# Account / trading DTOs
# ---------------------------------------------------------------------------
class Order(_Base):
    """Carries both intent (set by us before sending) and broker-populated
    state (filled in via account events). Frozen, so an update is a new copy."""
    client_order_id: str              # our idempotency key, set before sending
    instrument: Instrument
    side: Side
    quantity: Decimal
    order_type: OrderType
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    tif: TimeInForce = TimeInForce.DAY
    # Broker-populated:
    broker_order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING_NEW
    filled_quantity: Decimal = Decimal(0)
    avg_fill_price: Optional[Decimal] = None
    ts_submitted: Optional[datetime] = None
    ts_updated: Optional[datetime] = None


class Fill(_Base):
    fill_id: str
    broker_order_id: str
    instrument: Instrument
    side: Side
    quantity: Decimal
    price: Decimal
    ts_event: datetime
    client_order_id: Optional[str] = None
    fee: Decimal = Decimal(0)


class Position(_Base):
    instrument: Instrument
    quantity: Decimal                 # signed: negative = short
    avg_price: Decimal
    ts_event: datetime
    market_price: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None


class Balance(_Base):
    cash: Decimal
    equity: Decimal                   # cash + market value of positions
    buying_power: Decimal
    ts_event: datetime
    currency: str = "USD"


# ---------------------------------------------------------------------------
# Derived value — output of the feature layer (factors, sentiment, ...).
# Exposed to the agent the same way market data is.
# ---------------------------------------------------------------------------
class FeatureValue(_Base):
    feature: str                      # e.g. "rsi_14", "rolling_vol_20", "news_sentiment"
    value: float
    ts_event: datetime                # the input-data timestamp this value is "as of"
    instrument: Optional[Instrument] = None   # None => market-wide / cross-sectional
    window: Optional[str] = None      # human-readable window/param tag
    meta: Optional[dict] = None


# ---------------------------------------------------------------------------
# Event envelope — the single thing that flows on the bus.
# ---------------------------------------------------------------------------
PayloadT = TypeVar("PayloadT")


class Event(_Base, Generic[PayloadT]):
    """Uniform envelope for everything on the bus.

    ts_event vs ts_ingest: ts_event is when the fact happened at the source;
    ts_ingest is when WE received/normalized it. Keep both — their gap is your
    latency signal, and a faithful replay must order by ts_event.
    """
    type: EventType
    source: str                       # which adapter/source produced this
    payload: PayloadT
    ts_event: datetime
    ts_ingest: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: UUID = Field(default_factory=uuid4)
    seq: Optional[int] = None         # per-source monotonic seq, for ordering/replay


# Convenience aliases for typed handlers in adapters/services:
QuoteEvent = Event[Quote]
BarEvent = Event[Bar]
NewsEvent = Event[NewsItem]
FillEvent = Event[Fill]
OrderEvent = Event[Order]
PositionEvent = Event[Position]
BalanceEvent = Event[Balance]
FeatureEvent = Event[FeatureValue]
