from __future__ import annotations

from typing import AsyncIterator
from contracts import (
    Position, Balance, Order, Event, AccountSourcePort, Bus, AccountService as AccountServiceInterface
)
from guardrail import Guardrail


class AccountService(AccountServiceInterface):
    """Manages trading sessions across multiple accounts/brokers, tracking state and sending alerts."""

    def __init__(self, sources: list[AccountSourcePort], bus: Bus, guardrail: Guardrail) -> None:
        """Initialize AccountService with brokerage sources, event bus, and risk guardrails."""
        self.sources = sources
        self.bus = bus
        self.guardrail = guardrail

    async def start(self) -> None:
        """Start the account service and connect all broker portals."""
        raise NotImplementedError()

    async def stop(self) -> None:
        """Stop the account service and disconnect all broker portals."""
        raise NotImplementedError()

    async def get_positions(self) -> list[Position]:
        """Fetch all currently open positions across active brokers."""
        raise NotImplementedError()

    async def get_balance(self) -> Balance:
        """Fetch cash and buying power balances."""
        raise NotImplementedError()

    async def get_orders(self) -> list[Order]:
        """Fetch order history or current pending orders."""
        raise NotImplementedError()

    async def place_order(self, order: Order) -> Order:
        """MUST route through the guardrail: call guardrail.check(order) before touching any source."""
        raise NotImplementedError()

    async def cancel_order(self, broker_order_id: str) -> None:
        """Cancel a pending order."""
        raise NotImplementedError()

    def subscribe(self) -> AsyncIterator[Event]:
        """Subscribe to account-related stream events (fills, balances, positions)."""
        raise NotImplementedError()
