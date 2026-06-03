"""
apps/smoke/mock_adapter.py — MockAccountAdapter: no network, deterministic fake
data. Lets you exercise the WHOLE pipeline (adapter -> service -> bus -> tool)
without credentials. Swap this for the real AlpacaAccountAdapter to test live —
nothing else in the slice changes. That substitutability IS the point.

It does not subclass BaseAccountAdapter (no need to test the base here); it just
satisfies AccountSourcePort structurally and emits a couple of fake fills so you
can see events traverse the bus.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from contracts import (
    AssetClass,
    Balance,
    Event,
    EventType,
    Fill,
    Instrument,
    Order,
    OrderStatus,
    Position,
    Side,
    SourceCapabilities,
    SourceMode,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


_AAPL = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)


class MockAccountAdapter:  # satisfies AccountSourcePort structurally
    name = "mock"

    def __init__(self, *, n_fills: int = 3, interval_s: float = 0.3, **params) -> None:
        self._n_fills = n_fills
        self._interval = interval_s

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            mode=SourceMode.PUSH,
            supports_streaming=True,
            asset_classes=(AssetClass.EQUITY,),
        )

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def health(self) -> bool:
        return True

    async def get_balance(self) -> Balance:
        return Balance(
            cash=Decimal("100000"),
            equity=Decimal("100000"),
            buying_power=Decimal("200000"),
            ts_event=_now(),
        )

    async def get_positions(self) -> list[Position]:
        return [
            Position(
                instrument=_AAPL,
                quantity=Decimal("10"),
                avg_price=Decimal("190.00"),
                ts_event=_now(),
            )
        ]

    async def get_orders(self) -> list[Order]:
        return []

    async def place_order(self, order: Order) -> Order:
        return order.model_copy(
            update={
                "broker_order_id": "mock-broker-1",
                "status": OrderStatus.FILLED,
                "filled_quantity": order.quantity,
                "avg_fill_price": Decimal("190.05"),
                "ts_submitted": _now(),
                "ts_updated": _now(),
            }
        )

    async def cancel_order(self, broker_order_id: str) -> None:
        pass

    async def subscribe(self) -> AsyncIterator[Event]:
        """Emit a few fake fills so the slice has something flowing on the bus."""
        for i in range(self._n_fills):
            await asyncio.sleep(self._interval)
            fill = Fill(
                fill_id=f"mock-fill-{i}",
                broker_order_id="mock-broker-1",
                instrument=_AAPL,
                side=Side.BUY,
                quantity=Decimal("1"),
                price=Decimal("190.00") + Decimal(i) / 100,
                ts_event=_now(),
            )
            yield Event(
                type=EventType.FILL,
                source=self.name,
                payload=fill,
                ts_event=fill.ts_event,
            )
