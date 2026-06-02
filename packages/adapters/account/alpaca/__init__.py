"""Alpaca account and execution adapter.

Alpaca is hybrid: account snapshots and order commands are pull-based REST
calls through `TradingClient`, while order/fill updates arrive over the
`TradingStream.subscribe_trade_updates()` websocket. The adapter keeps that
split explicit: `BaseAccountAdapter` owns the shared account flow and
idempotency, while this module owns Alpaca client construction, raw API calls,
stream callback bridging, and conversion into the normalized contract DTOs.

Secrets are already resolved by the config layer and arrive as constructor
kwargs. This module deliberately does not read `.env` or `os.environ`; it only
accepts Alpaca-specific settings such as `api_key`, `api_secret`, `paper`,
`base_url`, and `raw_data`.

The implementation accepts both `raw_data=True` dictionaries and normal
alpaca-py Pydantic-style model objects. Alpaca statuses and asset classes are
mapped into the smaller project-wide enums, and trade-update `fill` events are
emitted as `EventType.FILL`; other trade updates become `EventType.ORDER_UPDATE`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Protocol, TYPE_CHECKING

from adapters._base import BaseAccountAdapter
from contracts import (
    AssetClass,
    Balance,
    Event,
    EventType,
    Fill,
    Instrument,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TimeInForce,
)
from plugins import register

if TYPE_CHECKING:
    from alpaca.trading.requests import OrderRequest


class _TradingClientLike(Protocol):
    """Small alpaca-py surface this adapter needs.

    Keeping the constructor injection structural lets unit tests provide narrow
    fakes without weakening the runtime path: `_build_trading_client()` still
    constructs the real `alpaca.trading.client.TradingClient`.
    """

    def get_all_positions(self) -> Any: ...
    def get_account(self) -> Any: ...
    def get_orders(self, filter: Any = None) -> Any: ...
    def submit_order(self, order_data: Any) -> Any: ...
    def cancel_order_by_id(self, order_id: str) -> Any: ...
    def get_clock(self) -> Any: ...


class _TradingStreamLike(Protocol):
    """Small `TradingStream` surface used by subscribe/disconnect."""

    def subscribe_trade_updates(self, handler: Any) -> Any: ...
    def run(self) -> Any: ...


@register("account", "alpaca")
class AlpacaAccountAdapter(BaseAccountAdapter):
    """Live Alpaca account/execution adapter skeleton.

    Credentials and non-secret settings are supplied by the config layer as
    constructor kwargs. This adapter does not read environment variables.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        paper: bool = True,
        base_url: str | None = None,
        raw_data: bool = False,
        trading_client: _TradingClientLike | None = None,
        trading_stream: _TradingStreamLike | None = None,
        rate_limit: int = 100,
        **params: Any,
    ) -> None:
        self.stream_url = params.pop("stream_url", None)
        base_url = self._normalize_rest_base_url(base_url)

        super().__init__(
            name="AlpacaAccountAdapter",
            rate_limit=rate_limit,
            **params,
        )
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.base_url = base_url
        self.raw_data = raw_data
        self.trading_client = trading_client
        self.trading_stream = trading_stream

    # --- lifecycle hooks for BaseAdapter ---
    async def _connect(self) -> None:
        if self.trading_client is None:
            self.trading_client = self._build_trading_client()
        if self.trading_stream is None:
            self.trading_stream = self._build_trading_stream()

    async def _disconnect(self) -> None:
        if self.trading_stream is not None:
            stop = getattr(self.trading_stream, "stop", None)
            close = getattr(self.trading_stream, "close", None)
            stop_ws = getattr(self.trading_stream, "stop_ws", None)
            stream_loop = getattr(self.trading_stream, "_loop", None)
            if callable(stop) and getattr(stream_loop, "is_running", lambda: False)():
                await self._maybe_await(stop())
            elif callable(close):
                await self._maybe_await(close())
            elif callable(stop_ws):
                await self._maybe_await(stop_ws())

    async def _check_health(self) -> bool:
        if self.trading_client is None:
            return False

        try:
            # Use Alpaca's lightweight authenticated clock endpoint for liveness.
            # Do not build a client here: health should inspect the lifecycle
            # state created by start()/injection, not silently initialize it.
            await asyncio.to_thread(self.trading_client.get_clock)
        except Exception:
            return False
        return True

    # --- alpaca-py client construction ---
    def _build_trading_client(self) -> _TradingClientLike:
        # Keep optional Alpaca SDK imports lazy so registry discovery still works
        # when the project is installed without the `alpaca` extra.
        from alpaca.trading.client import TradingClient

        return TradingClient(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=self.paper,
            raw_data=self.raw_data,
            url_override=self.base_url,
        )

    def _build_trading_stream(self) -> _TradingStreamLike:
        # TradingStream requires key+secret; TradingClient can also be used with
        # other auth modes later, but this adapter's config path currently emits
        # ALPACA_API_KEY / ALPACA_API_SECRET as api_key / api_secret kwargs.
        from alpaca.trading.stream import TradingStream

        if self.api_key is None or self.api_secret is None:
            raise ValueError("api_key and api_secret are required for Alpaca streaming")
        return TradingStream(
            api_key=self.api_key,
            secret_key=self.api_secret,
            paper=self.paper,
            raw_data=self.raw_data,
            url_override=self.stream_url,
        )

    # --- AccountSourcePort stream ---
    async def subscribe(self) -> AsyncIterator[Event]:
        async for raw in self._subscribe_raw():
            yield self._event_from_raw(raw)

    # --- hooks required by BaseAccountAdapter ---
    async def _fetch_positions_raw(self) -> list[dict]:
        client = self._require_trading_client()
        positions = await asyncio.to_thread(client.get_all_positions)
        return [self._as_dict(position) for position in positions]

    async def _fetch_balance_raw(self) -> dict:
        account = await asyncio.to_thread(self._require_trading_client().get_account)
        return self._as_dict(account)

    async def _fetch_orders_raw(self) -> list[dict]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        request = GetOrdersRequest(status=QueryOrderStatus.ALL)
        orders = await asyncio.to_thread(
            self._require_trading_client().get_orders,
            filter=request,
        )
        return [self._as_dict(order) for order in orders]

    async def _submit_raw(self, order: Order) -> dict:
        submitted = await asyncio.to_thread(
            self._require_trading_client().submit_order,
            self._to_alpaca_order_request(order),
        )
        return self._as_dict(submitted)

    async def _cancel_raw(self, broker_order_id: str) -> None:
        await asyncio.to_thread(
            self._require_trading_client().cancel_order_by_id,
            broker_order_id,
        )

    async def _subscribe_raw(self) -> AsyncIterator[dict[str, Any]]:
        stream = self._require_trading_stream()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        async def handle_trade_update(update: Any) -> None:
            # alpaca-py runs the websocket loop in the worker thread below. Hop
            # back to this adapter's event loop before touching the asyncio queue.
            loop.call_soon_threadsafe(queue.put_nowait, self._as_dict(update))

        stream.subscribe_trade_updates(handle_trade_update)
        task = asyncio.create_task(asyncio.to_thread(stream.run))

        try:
            while True:
                if task.done() and queue.empty():
                    exc = task.exception()
                    if exc is not None:
                        raise exc
                    break
                raw = await queue.get()
                if raw is None:
                    break
                yield raw
        finally:
            stop = getattr(stream, "stop", None)
            if callable(stop):
                stop()
            task.cancel()

    # --- alpaca-py request conversion ---
    def _to_alpaca_order_request(self, order: Order) -> OrderRequest:
        # Imports stay local for the same optional-extra reason as client
        # construction. Return concrete request subclasses; alpaca-py accepts the
        # shared OrderRequest base in TradingClient.submit_order().
        from alpaca.trading.enums import OrderType as AlpacaOrderType
        from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLimitOrderRequest,
            StopOrderRequest,
        )

        kwargs = {
            "symbol": order.instrument.symbol,
            "qty": float(order.quantity),
            "side": self._map_side_to_alpaca(order.side),
            "time_in_force": AlpacaTimeInForce(order.tif.value),
            "client_order_id": order.client_order_id,
        }

        if order.order_type is OrderType.MARKET:
            return MarketOrderRequest(type=AlpacaOrderType.MARKET, **kwargs)
        if order.order_type is OrderType.LIMIT:
            if order.limit_price is None:
                raise ValueError("limit_price is required for limit orders")
            return LimitOrderRequest(
                type=AlpacaOrderType.LIMIT,
                limit_price=float(order.limit_price),
                **kwargs,
            )
        if order.order_type is OrderType.STOP:
            if order.stop_price is None:
                raise ValueError("stop_price is required for stop orders")
            return StopOrderRequest(
                type=AlpacaOrderType.STOP,
                stop_price=float(order.stop_price),
                **kwargs,
            )
        if order.order_type is OrderType.STOP_LIMIT:
            if order.limit_price is None or order.stop_price is None:
                raise ValueError(
                    "limit_price and stop_price are required for stop-limit orders"
                )
            return StopLimitOrderRequest(
                type=AlpacaOrderType.STOP_LIMIT,
                limit_price=float(order.limit_price),
                stop_price=float(order.stop_price),
                **kwargs,
            )

        raise ValueError(f"Unsupported order type: {order.order_type}")

    # --- source-specific normalization ---
    def _normalize_order(self, raw: dict) -> Order:
        return Order(
            client_order_id=str(raw.get("client_order_id") or raw.get("id")),
            instrument=self._map_instrument(raw),
            side=self._map_side(str(self._value(raw.get("side")))),
            quantity=self._decimal(raw.get("qty") or raw.get("quantity")),
            order_type=self._map_order_type(
                str(self._value(raw.get("type") or raw.get("order_type")))
            ),
            limit_price=self._optional_decimal(raw.get("limit_price")),
            stop_price=self._optional_decimal(raw.get("stop_price")),
            tif=self._map_time_in_force(
                str(self._value(raw.get("time_in_force") or raw.get("tif")))
            ),
            broker_order_id=str(raw["id"]) if raw.get("id") is not None else None,
            status=self._map_status(str(self._value(raw.get("status")))),
            filled_quantity=self._decimal(raw.get("filled_qty") or 0),
            avg_fill_price=self._optional_decimal(raw.get("filled_avg_price")),
            ts_submitted=self._parse_optional_timestamp(raw.get("submitted_at")),
            ts_updated=self._parse_optional_timestamp(raw.get("updated_at")),
        )

    def _normalize_fill(self, raw: dict) -> Fill:
        order_raw = self._as_dict(raw.get("order", {}))
        ts_event = self._parse_timestamp(raw.get("timestamp") or raw.get("ts_event"))
        return Fill(
            fill_id=str(raw.get("execution_id") or raw.get("id")),
            broker_order_id=str(
                raw.get("order_id") or order_raw.get("id") or raw.get("broker_order_id")
            ),
            instrument=self._map_instrument(order_raw or raw),
            side=self._map_side(
                str(self._value(order_raw.get("side") or raw.get("side")))
            ),
            quantity=self._decimal(raw.get("qty") or raw.get("quantity")),
            price=self._decimal(raw.get("price")),
            ts_event=ts_event,
            client_order_id=order_raw.get("client_order_id")
            or raw.get("client_order_id"),
            fee=self._decimal(raw.get("fee") or raw.get("commission") or 0),
        )

    def _normalize_position(self, raw: dict) -> Position:
        quantity = self._decimal(raw.get("qty") or raw.get("quantity"))
        if str(self._value(raw.get("side"))).lower() == "short":
            quantity = -abs(quantity)
        return Position(
            instrument=self._map_instrument(raw),
            quantity=quantity,
            avg_price=self._decimal(raw.get("avg_entry_price") or raw.get("avg_price")),
            ts_event=self._parse_timestamp(
                raw.get("updated_at")
                or raw.get("created_at")
                or datetime.now(timezone.utc)
            ),
            market_price=self._optional_decimal(raw.get("current_price")),
            unrealized_pnl=self._optional_decimal(raw.get("unrealized_pl")),
        )

    def _normalize_balance(self, raw: dict) -> Balance:
        return Balance(
            cash=self._decimal(raw.get("cash")),
            equity=self._decimal(raw.get("equity") or raw.get("portfolio_value")),
            buying_power=self._decimal(raw.get("buying_power")),
            ts_event=self._parse_timestamp(
                raw.get("updated_at")
                or raw.get("created_at")
                or datetime.now(timezone.utc)
            ),
            currency=str(raw.get("currency") or "USD"),
        )

    def _map_status(self, raw_status: str) -> OrderStatus:
        # Alpaca has more intermediate states than the project contract. Collapse
        # active/replacement states to NEW or PENDING_NEW; preserve terminal states.
        status = raw_status.lower()
        if status in {"pending_new", "accepted", "pending_review", "held"}:
            return OrderStatus.PENDING_NEW
        if status in {
            "new",
            "open",
            "accepted_for_bidding",
            "pending_cancel",
            "pending_replace",
            "done_for_day",
            "stopped",
            "calculated",
        }:
            return OrderStatus.NEW
        if status == "partially_filled":
            return OrderStatus.PARTIALLY_FILLED
        if status == "filled":
            return OrderStatus.FILLED
        if status in {"canceled", "replaced"}:
            return OrderStatus.CANCELED
        if status == "expired":
            return OrderStatus.EXPIRED
        if status in {"rejected", "suspended"}:
            return OrderStatus.REJECTED
        raise ValueError(f"Unsupported Alpaca order status: {raw_status}")

    def _map_instrument(self, raw: dict[str, Any]) -> Instrument:
        symbol = raw.get("symbol")
        if not symbol:
            raise ValueError("Alpaca payload is missing symbol")
        return Instrument(
            symbol=str(symbol),
            asset_class=self._map_asset_class(raw.get("asset_class")),
            exchange=str(self._value(raw["exchange"])) if raw.get("exchange") else None,
            currency=str(raw.get("currency") or "USD"),
        )

    def _map_side(self, raw_side: str) -> Side:
        return Side(str(self._value(raw_side)).lower())

    def _map_order_type(self, raw_order_type: str) -> OrderType:
        return OrderType(str(self._value(raw_order_type)).lower())

    def _map_time_in_force(self, raw_tif: str) -> TimeInForce:
        return TimeInForce(str(self._value(raw_tif)).lower())

    def _parse_timestamp(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        raise ValueError(f"Cannot parse timestamp from {value!r}")

    def _event_from_raw(self, raw: dict[str, Any]) -> Event:
        event_name = str(self._value(raw.get("event"))).lower()
        if event_name in {"fill", "partial_fill"}:
            payload = self._normalize_fill(raw)
            return Event(
                type=EventType.FILL,
                source=self.name,
                payload=payload,
                ts_event=payload.ts_event,
            )

        order = self._normalize_order(self._as_dict(raw.get("order", raw)))
        return Event(
            type=EventType.ORDER_UPDATE,
            source=self.name,
            payload=order,
            ts_event=order.ts_updated
            or order.ts_submitted
            or datetime.now(timezone.utc),
        )

    # --- internal helpers ---
    def _require_trading_client(self) -> _TradingClientLike:
        if self.trading_client is None:
            self.trading_client = self._build_trading_client()
        return self.trading_client

    def _require_trading_stream(self) -> _TradingStreamLike:
        if self.trading_stream is None:
            self.trading_stream = self._build_trading_stream()
        return self.trading_stream

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if hasattr(value, "__await__"):
            return await value
        return value

    @classmethod
    def _as_dict(cls, raw: Any) -> dict:
        # raw_data=True returns dicts. The default alpaca-py path returns
        # Pydantic-style models. Normalize both at the adapter boundary.
        if isinstance(raw, dict):
            return raw
        if hasattr(raw, "model_dump"):
            return raw.model_dump()
        if hasattr(raw, "dict"):
            return raw.dict()
        return vars(raw)

    @classmethod
    def _value(cls, value: Any) -> Any:
        return getattr(value, "value", value)

    @classmethod
    def _decimal(cls, value: Any) -> Decimal:
        if value is None:
            raise ValueError("Expected decimal-compatible value, got None")
        return Decimal(str(cls._value(value)))

    @classmethod
    def _optional_decimal(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        return cls._decimal(value)

    def _parse_optional_timestamp(self, value: Any) -> datetime | None:
        if value is None:
            return None
        return self._parse_timestamp(value)

    def _map_asset_class(self, raw_asset_class: Any) -> AssetClass:
        asset_class = str(self._value(raw_asset_class or "us_equity")).lower()
        if asset_class in {"us_equity", "equity"}:
            return AssetClass.EQUITY
        if asset_class in {"us_option", "option"}:
            return AssetClass.OPTION
        if asset_class == "crypto":
            return AssetClass.CRYPTO
        raise ValueError(f"Unsupported Alpaca asset class: {raw_asset_class}")

    @staticmethod
    def _map_side_to_alpaca(side: Side) -> Any:
        from alpaca.trading.enums import OrderSide

        return OrderSide(side.value)

    @staticmethod
    def _normalize_rest_base_url(base_url: str | None) -> str | None:
        if base_url is None:
            return None

        # alpaca-py TradingClient appends "/v2" to url_override internally. The
        # config value may include it because that is the public REST endpoint.
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v2"):
            return normalized[:-3]
        return normalized
