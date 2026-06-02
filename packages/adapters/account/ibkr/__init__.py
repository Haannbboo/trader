from decimal import Decimal
from datetime import datetime, timezone
from typing import AsyncIterator, List
from contracts import Position, Balance, Order, Event, AccountSourcePort
from plugins import register
from adapters._base import BaseAdapter


@register("account", "ibkr")
class IBKRAccountAdapter(BaseAdapter, AccountSourcePort):
    """Interactive Brokers Account and Execution Adapter (Skeleton)."""

    def __init__(self) -> None:
        super().__init__(name="IBKRAccountAdapter", rate_limit=50)

    async def get_positions(self) -> List[Position]:
        return []

    async def get_balance(self) -> Balance:
        return Balance(
            cash=Decimal("100000.00"),
            equity=Decimal("100000.00"),
            buying_power=Decimal("100000.00"),
            ts_event=datetime.now(timezone.utc),
            currency="USD"
        )

    async def get_orders(self) -> List[Order]:
        return []

    async def place_order(self, order: Order) -> Order:
        raise NotImplementedError()

    async def cancel_order(self, broker_order_id: str) -> None:
        raise NotImplementedError()

    async def subscribe(self) -> AsyncIterator[Event]:
        if False:
            yield
