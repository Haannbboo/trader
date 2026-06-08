from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from contracts.schema import (
    AssetClass,
    Balance,
    Bar,
    Event,
    EventType,
    FeatureValue,
    Fill,
    Instrument,
    NewsItem,
    Order,
    Position,
    Quote,
    Timeframe,
)


# ---------------------------------------------------------------------------
# Capability & filter contract objects (data, so: pydantic)
# ---------------------------------------------------------------------------
class SourceMode(str, Enum):
    PUSH = "push"  # native streaming (websocket / SSE)
    POLL = "poll"  # adapter polls and synthesizes a stream


class SourceCapabilities(BaseModel):
    """Every source DECLARES what it can do, so the service layer can route,
    failover, and set SLAs without hardcoding per-source knowledge."""

    mode: SourceMode
    supports_streaming: bool
    asset_classes: tuple[AssetClass, ...]
    historical: bool = False  # can it serve get_bars / query over history?
    rate_limit_per_sec: Optional[float] = None
    latency_hint_ms: Optional[float] = None


class MarketChannel(str, Enum):
    QUOTES = "quotes"
    TRADES = "trades"
    BARS = "bars"


class NewsFilter(BaseModel):
    instruments: tuple[Instrument, ...] = ()
    sources: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    since: Optional[datetime] = None


class Subscription(BaseModel):
    """Bus-level filter. An empty tuple on a dimension means 'match all'."""

    event_types: tuple[EventType, ...] = ()
    instruments: tuple[Instrument, ...] = ()
    sources: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Source ports — what each ADAPTER implements (the parallel ③-layer chunks)
# ---------------------------------------------------------------------------
@runtime_checkable
class SourcePort(Protocol):
    """Lifecycle shared by every data source."""

    name: str

    @property
    def capabilities(self) -> SourceCapabilities: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def health(self) -> bool: ...


@runtime_checkable
class MarketSourcePort(SourcePort, Protocol):
    async def get_quote(self, instrument: Instrument) -> Quote: ...

    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]: ...

    def subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]: ...


@runtime_checkable
class NewsSourcePort(SourcePort, Protocol):
    async def query(self, flt: NewsFilter) -> list[NewsItem]: ...
    def subscribe(self, flt: NewsFilter) -> AsyncIterator[Event]: ...


@runtime_checkable
class AccountSourcePort(SourcePort, Protocol):
    async def get_positions(self) -> list[Position]: ...
    async def get_balance(self) -> Balance: ...
    async def get_orders(self) -> list[Order]: ...
    async def place_order(self, order: Order) -> Order: ...
    async def cancel_order(self, broker_order_id: str) -> None: ...
    def subscribe(self) -> AsyncIterator[Event]: ...  # fills + order/position updates


# ---------------------------------------------------------------------------
# Bus — implemented by I2 (in-process asyncio first; Redis Streams later)
# ---------------------------------------------------------------------------
@runtime_checkable
class Bus(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, event: Event) -> None: ...

    def subscribe(
        self,
        subscription: Subscription,
        *,
        group: Optional[str] = None,
    ) -> AsyncIterator[Event]: ...

    def replay(
        self,
        subscription: Subscription,
        start: datetime,
        end: datetime,
        *,
        history: HistoryStore,
    ) -> AsyncIterator[Event]: ...

    # `group`: durable buses use it for consumer-group fan-out / replay.
    # The in-process bus may ignore it. Keep it in the contract so callers
    # written now don't need changing when you swap implementations.


# ---------------------------------------------------------------------------
# Processor — the unit of the feature layer (each factor / model is one)
# ---------------------------------------------------------------------------
@runtime_checkable
class Processor(Protocol):
    """A single derivation: an RSI factor, a rolling-vol factor, a sentiment
    model, etc.

    HARD CONSTRAINTS — these are what make backtest == live:
      - Deterministic given the ordered sequence of input events.
      - No wall-clock reads, no network/disk I/O inside on_event. The runtime
        decides whether events arrive from the live bus or a historical replay;
        the processor must not be able to tell.
      - All state lives in the instance. `warmup_events` tells the runtime how
        many historical events to feed before the outputs should be trusted.

    `input` doubles as DAG wiring: a processor whose input includes FEATURE
    events of "returns" depends on the returns processor, so the runtime can
    order them.
    """

    name: str

    @property
    def input(self) -> Subscription: ...

    @property
    def warmup_events(self) -> int: ...

    async def on_event(
        self, event: Event
    ) -> list[Event]: ...  # emits 0+ FEATURE events


# ---------------------------------------------------------------------------
# Domain services — implemented by S-* chunks, consumed by the tool layer.
# Note these mirror the source ports but are AGGREGATED (multi-source routing,
# dedup, subscription multiplexing) and publish onto the bus.
# ---------------------------------------------------------------------------
@runtime_checkable
class MarketDataService(Protocol):
    async def get_quote(self, instrument: Instrument) -> Quote: ...
    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]: ...
    def subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]: ...


@runtime_checkable
class NewsService(Protocol):
    async def query(self, flt: NewsFilter) -> list[NewsItem]: ...
    def subscribe(self, flt: NewsFilter) -> AsyncIterator[Event]: ...


@runtime_checkable
class AccountService(Protocol):
    async def get_positions(self) -> list[Position]: ...
    async def get_balance(self) -> Balance: ...
    async def get_orders(self) -> list[Order]: ...
    async def place_order(
        self, order: Order
    ) -> Order: ...  # MUST route through the guardrail
    async def cancel_order(self, broker_order_id: str) -> None: ...
    def subscribe(self) -> AsyncIterator[Event]: ...


@runtime_checkable
class FeatureService(Protocol):
    """Exposes derived values to the tool layer the SAME way market data is
    exposed — to the agent a factor looks like just another queryable /
    subscribable field, even though it's computed by Processors under the hood."""

    async def get_value(
        self,
        feature: str,
        instrument: Optional[Instrument] = None,
    ) -> FeatureValue: ...
    def subscribe(self, features: list[str]) -> AsyncIterator[Event]: ...


# ---------------------------------------------------------------------------
# Persistence — read face of the storage layer. Implemented by the
# Repository in packages/infra/persistence. Services depend on this Protocol,
# not on Repository directly, so the storage backend is swappable.
# ---------------------------------------------------------------------------
@runtime_checkable
class HistoryStore(Protocol):
    """Read-only access to stored raw facts (bars, news, fills).

    Returned values are schema DTOs (frozen pydantic), never ORM rows — the
    boundary the persistence package's docstring already states.

    Empty result sets return []. Connection / pool errors propagate natively
    (services may retry); unrecoverable data-shape problems raise
    contracts.errors.PersistenceError.
    """

    async def fetch_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]: ...

    async def fetch_news(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[NewsItem]: ...

    async def fetch_fills(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        broker_order_id: Optional[str] = None,
    ) -> list[Fill]: ...
