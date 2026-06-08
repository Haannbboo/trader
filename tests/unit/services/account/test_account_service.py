from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from account import AccountService
from contracts import (
    AssetClass,
    Balance,
    Event,
    EventType,
    Instrument,
    Order,
    OrderFilter,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TimeInForce,
)
from guardrail import Guardrail


class MockAccountSource:
    name = "mock_broker"

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.positions = [
            Position(
                instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
                quantity=Decimal("10"),
                avg_price=Decimal("150"),
                ts_event=datetime.now(timezone.utc),
                unrealized_pnl=Decimal("50"),
            )
        ]
        self.balance = Balance(
            cash=Decimal("5000"),
            equity=Decimal("6550"),
            buying_power=Decimal("10000"),
            ts_event=datetime.now(timezone.utc),
        )
        self.orders = []
        self.placed_order = None
        self.cancelled_broker_order_id = None
        self.events_to_yield: list[Event] = []
        self.last_orders_status: OrderFilter | None = None
        self.last_orders_symbols: list[str] | None = None

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def get_positions(self) -> list[Position]:
        return self.positions

    async def get_balance(self) -> Balance:
        return self.balance

    async def get_orders(
        self,
        *,
        status: OrderFilter = OrderFilter.OPEN,
        symbols: list[str] | None = None,
    ) -> list[Order]:
        self.last_orders_status = status
        self.last_orders_symbols = list(symbols) if symbols else None
        return self.orders

    async def place_order(self, order: Order) -> Order:
        self.placed_order = order
        return order.model_copy(
            update={
                "broker_order_id": "broker-123",
                "status": OrderStatus.PENDING_NEW,
            }
        )

    async def cancel_order(self, broker_order_id: str) -> None:
        self.cancelled_broker_order_id = broker_order_id

    async def subscribe(self) -> AsyncIterator[Event]:
        for event in self.events_to_yield:
            yield event


class MockBus:
    def __init__(self) -> None:
        self.published_events: list[Event] = []
        self.subscription = None

    async def publish(self, event: Event) -> None:
        self.published_events.append(event)

    def subscribe(self, subscription) -> AsyncIterator[Event]:
        self.subscription = subscription

        class EmptyIterator:
            def __aiter__(self) -> EmptyIterator:
                return self

            async def __anext__(self) -> Event:
                raise StopAsyncIteration

        return EmptyIterator()


def test_account_service_init_validation() -> None:
    bus = MockBus()
    guardrail = Guardrail([])

    # Zero sources
    with pytest.raises(ValueError, match="requires at least one source"):
        AccountService([], bus, guardrail)

    # More than one source
    source1 = MockAccountSource()
    source2 = MockAccountSource()
    with pytest.raises(ValueError, match="only supports a single source in v1"):
        AccountService([source1, source2], bus, guardrail)


@pytest.mark.asyncio
async def test_account_service_reads_proxy() -> None:
    source = MockAccountSource()
    bus = MockBus()
    guardrail = Guardrail([])
    service = AccountService([source], bus, guardrail)

    # Positions
    positions = await service.get_positions()
    assert positions == source.positions

    # Balance
    balance = await service.get_balance()
    assert balance == source.balance

    # Orders (default to OPEN, no symbol filter)
    orders = await service.get_orders()
    assert orders == source.orders
    assert source.last_orders_status == OrderFilter.OPEN
    assert source.last_orders_symbols is None

    # Orders with explicit filter — service must pass it through verbatim.
    await service.get_orders(status=OrderFilter.ALL, symbols=["AAPL"])
    assert source.last_orders_status == OrderFilter.ALL
    assert source.last_orders_symbols == ["AAPL"]

    # Cancel
    await service.cancel_order("broker-123")
    assert source.cancelled_broker_order_id == "broker-123"


@pytest.mark.asyncio
async def test_account_service_place_order_flow() -> None:
    source = MockAccountSource()
    bus = MockBus()
    guardrail = Guardrail([])  # No rules
    service = AccountService([source], bus, guardrail)

    order = Order(
        client_order_id="client-abc",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("5"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150"),
        tif=TimeInForce.DAY,
    )

    result = await service.place_order(order)
    assert result.broker_order_id == "broker-123"
    assert source.placed_order == order

    # Verify published event on the bus
    assert len(bus.published_events) == 1
    event = bus.published_events[0]
    assert event.type == EventType.ORDER_UPDATE
    assert event.source == "mock_broker"
    assert event.payload == result


@pytest.mark.asyncio
async def test_account_service_pump() -> None:
    source = MockAccountSource()
    bus = MockBus()
    guardrail = Guardrail([])
    service = AccountService([source], bus, guardrail)

    test_event = Event(
        type=EventType.FILL,
        source="mock_broker",
        payload=Position(
            instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
            quantity=Decimal("10"),
            avg_price=Decimal("150"),
            ts_event=datetime.now(timezone.utc),
            unrealized_pnl=Decimal("50"),
        ),
        ts_event=datetime.now(timezone.utc),
    )
    source.events_to_yield = [test_event]

    await service.start()
    assert source.started

    # Wait a tiny bit for the async pump loop task to execute
    await asyncio.sleep(0.05)

    await service.stop()
    assert source.stopped

    # Event should have been pumped to the bus
    assert test_event in bus.published_events
