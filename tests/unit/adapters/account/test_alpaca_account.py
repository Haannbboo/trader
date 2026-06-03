from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Coroutine

import pytest
from adapters.account.alpaca import AlpacaAccountAdapter
from contracts import (
    AssetClass,
    EventType,
    Instrument,
    Order,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "alpaca"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURE_ROOT / name).read_text())


class FakeTradingClient:
    def __init__(self) -> None:
        self.account = load_fixture("account.json")
        self.positions = load_fixture("positions.json")
        self.orders = load_fixture("orders.json")
        self.submitted_order: Any | None = None
        self.canceled_order_id: str | None = None
        self.order_filter: Any | None = None
        self.clock_calls = 0
        self.clock_error: Exception | None = None

    def get_clock(self) -> dict[str, str]:
        self.clock_calls += 1
        if self.clock_error is not None:
            raise self.clock_error
        return {"timestamp": "2026-06-02T00:00:00Z"}

    def get_account(self) -> dict:
        return self.account

    def get_all_positions(self) -> list[dict]:
        return self.positions

    def get_orders(self, filter: Any = None) -> list[dict]:
        self.order_filter = filter
        return self.orders

    def submit_order(self, order_data: Any) -> dict:
        self.submitted_order = order_data
        return {
            **self.orders[0],
            "id": "submitted-1",
            "client_order_id": order_data.client_order_id,
            "status": "accepted",
        }

    def cancel_order_by_id(self, order_id: str) -> None:
        self.canceled_order_id = order_id


class FakeTradingStream:
    def __init__(self) -> None:
        self.handler: Callable[[Any], Coroutine[Any, Any, None]] | None = None
        self.stopped = False
        self.trade_update = load_fixture("trade_update_fill.json")

    def subscribe_trade_updates(
        self,
        handler: Callable[[Any], Coroutine[Any, Any, None]],
    ) -> None:
        self.handler = handler

    def run(self) -> None:
        assert self.handler is not None
        asyncio.run(self.handler(self.trade_update))

    def stop(self) -> None:
        self.stopped = True


class BuildTrackingAlpacaAccountAdapter(AlpacaAccountAdapter):
    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.build_client_calls = 0

    def _build_trading_client(self) -> Any:
        self.build_client_calls += 1
        return super()._build_trading_client()


@pytest.mark.asyncio
async def test_alpaca_account_polling_read_path_normalizes_fixture_contracts() -> None:
    client = FakeTradingClient()
    adapter = AlpacaAccountAdapter(
        api_key="key",
        api_secret="secret",
        trading_client=client,
    )

    balance = await adapter.get_balance()
    positions = await adapter.get_positions()
    orders = await adapter.get_orders()

    assert balance.cash == Decimal("99694.41")
    assert balance.equity == Decimal("99999.63")
    assert balance.buying_power == Decimal("199694.04")
    assert positions[0].instrument.symbol == "AAPL"
    assert positions[0].quantity == Decimal("1")
    assert positions[0].avg_price == Decimal("305.59")
    assert orders[0].broker_order_id == "fixture_id"
    assert orders[0].status == OrderStatus.FILLED


def test_alpaca_account_uses_api_secret_and_normalizes_rest_url() -> None:
    adapter = AlpacaAccountAdapter(
        api_key="key",
        api_secret="secret",
        base_url="https://paper-api.alpaca.markets/v2",
    )

    assert adapter.api_secret == "secret"
    assert adapter.base_url == "https://paper-api.alpaca.markets"
    assert adapter.stream_url is None


@pytest.mark.asyncio
async def test_alpaca_account_health_does_not_build_missing_client() -> None:
    adapter = BuildTrackingAlpacaAccountAdapter(api_key="key", api_secret="secret")
    adapter._started = True

    assert await adapter.health() is False
    assert adapter.build_client_calls == 0
    assert adapter.trading_client is None


@pytest.mark.asyncio
async def test_alpaca_account_health_uses_clock_probe_on_existing_client() -> None:
    client = FakeTradingClient()
    adapter = AlpacaAccountAdapter(
        api_key="key",
        api_secret="secret",
        trading_client=client,
    )
    adapter._started = True

    assert await adapter.health() is True
    assert client.clock_calls == 1

    client.clock_error = RuntimeError("401")

    assert await adapter.health() is False
    assert client.clock_calls == 2


@pytest.mark.asyncio
async def test_alpaca_account_places_and_cancels_orders() -> None:
    client = FakeTradingClient()
    adapter = AlpacaAccountAdapter(
        api_key="key",
        api_secret="secret",
        trading_client=client,
    )
    order = Order(
        client_order_id="client-2",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("2"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("189.50"),
        tif=TimeInForce.DAY,
    )

    submitted = await adapter.place_order(order)
    await adapter.cancel_order("submitted-1")

    assert client.submitted_order is not None
    assert client.submitted_order.symbol == "AAPL"
    assert client.submitted_order.qty == 2.0
    assert client.submitted_order.limit_price == 189.5
    assert submitted.broker_order_id == "submitted-1"
    assert submitted.status == OrderStatus.PENDING_NEW
    assert client.canceled_order_id == "submitted-1"


@pytest.mark.asyncio
async def test_alpaca_account_streaming_read_path_normalizes_fixture_events() -> None:
    adapter = AlpacaAccountAdapter(
        api_key="key",
        api_secret="secret",
        trading_client=FakeTradingClient(),
        trading_stream=FakeTradingStream(),
    )

    event = await anext(adapter.subscribe())

    assert event.type == EventType.FILL
    assert event.source == "AlpacaAccountAdapter"
    assert event.payload.fill_id == "fixture_execution_id"
    assert event.payload.broker_order_id == "fixture_id"
    assert event.payload.price == Decimal("305.59")
