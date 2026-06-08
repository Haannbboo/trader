"""
services.account — AccountService v1. Single broker, explicit addressing,
guardrail on every order. Lives in packages/services/account in the repo.

DESIGN DECISION — NO smart order routing, ever.
Unlike market/news (where multiple sources are redundant providers of the SAME
data and the service may pick/merge), multiple account sources are DIFFERENT
REAL ACCOUNTS at different brokers. They are NOT interchangeable. The service
must NEVER decide "which broker is best for this order" — that would silently
move the user's money between accounts. If multiple brokers ever exist, they are
addressed EXPLICITLY by name (e.g. for_broker("alpaca")), never auto-selected.
v1 holds exactly one source; this docstring exists so nobody adds routing later.

Responsibilities (v1):
  - own ONE AccountSourcePort + the bus + the guardrail,
  - pump the source's subscribe() stream onto the bus,
  - reads (positions/balance/orders) proxy straight to the source,
  - place_order builds a RiskContext, calls guardrail.check FIRST, then submits
    the (possibly clamped) order, then publishes the resulting order update.

NOT in v1 (add when a real second broker / heavy stream appears): multi-account
addressing, subscription multiplexing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator

from contracts import (
    AccountService as AccountServiceInterface,
)
from contracts import (
    AccountSourcePort,
    Balance,
    Bus,
    Event,
    EventType,
    Order,
    OrderFilter,
    Position,
    Subscription,
)
from guardrail import Guardrail, RiskContext


class AccountService(AccountServiceInterface):
    """ONE source by design (see module docstring). Inject the guardrail —
    the service does not construct it (apps/* wires rules)."""

    def __init__(
        self, sources: list[AccountSourcePort], bus: Bus, guardrail: Guardrail
    ) -> None:
        """ONE source by design (see module docstring). Inject the guardrail —
        the service does not construct it (apps/* wires rules)."""
        if not sources:
            raise ValueError("AccountService requires at least one source")
        if len(sources) > 1:
            raise ValueError("AccountService only supports a single source in v1")
        self._source = sources[0]
        self._bus = bus
        self._guardrail = guardrail
        self._pump_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the source, then pump its normalized account events to the bus."""
        await self._source.start()
        self._pump_task = asyncio.create_task(self._pump())

    async def stop(self) -> None:
        """Stop the source and stop pumping events."""
        if self._pump_task:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None
        await self._source.stop()

    async def _pump(self) -> None:
        """Forward source.subscribe() events onto the bus verbatim."""
        try:
            async for event in self._source.subscribe():
                await self._bus.publish(event)
        except asyncio.CancelledError:
            pass

    # --- reads: straight proxy to the single account ---
    async def get_positions(self) -> list[Position]:
        """Fetch all currently open positions across active brokers."""
        return await self._source.get_positions()

    async def get_balance(self) -> Balance:
        """Fetch cash and buying power balances."""
        return await self._source.get_balance()

    async def get_orders(
        self,
        *,
        status: OrderFilter = OrderFilter.OPEN,
        symbols: list[str] | None = None,
    ) -> list[Order]:
        """Fetch orders filtered by status and/or symbols. Default to OPEN
        orders — that's the common read ('what's still working'). Filtering
        happens at the broker, not in this service."""
        return await self._source.get_orders(status=status, symbols=symbols)

    # --- the one path that can lose money: guardrail is mandatory ---
    async def place_order(self, order: Order) -> Order:
        """REQUIRED sequence:
          1. ctx = await self._build_risk_context()   (positions + buying power)
          2. checked = await self._guardrail.check(order, ctx)  # may raise / clamp
          3. result = await self._source.place_order(checked)
          4. publish Event(ORDER_UPDATE, result) to the bus
          5. return result
        Never submit `order` directly — always submit the guardrail's returned
        (possibly clamped) order. RiskRejected propagates to the caller."""
        ctx = await self._build_risk_context()
        checked = await self._guardrail.check(order, ctx)
        result = await self._source.place_order(checked)

        event = Event(
            type=EventType.ORDER_UPDATE,
            source=self._source.name,
            payload=result,
            ts_event=result.ts_updated
            or result.ts_submitted
            or datetime.now(timezone.utc),
        )
        await self._bus.publish(event)
        return result

    async def cancel_order(self, broker_order_id: str) -> None:
        """Cancel a pending order."""
        await self._source.cancel_order(broker_order_id)

    def subscribe(self) -> AsyncIterator[Event]:
        """Re-expose account events (fills + order/position/balance updates) off
        the bus for streaming consumers (agent, feature workers)."""
        sub = Subscription(
            event_types=(
                EventType.FILL,
                EventType.ORDER_UPDATE,
                EventType.POSITION_UPDATE,
                EventType.BALANCE_UPDATE,
            ),
            sources=(self._source.name,),
        )
        return self._bus.subscribe(sub)

    # --- internal ---
    async def _build_risk_context(self) -> RiskContext:
        """Snapshot the live state rules need: current positions + buying power.
        Built fresh per order so rules judge against up-to-date state."""
        positions, balance = await asyncio.gather(
            self.get_positions(), self.get_balance()
        )
        return RiskContext(positions=positions, buying_power=balance.buying_power)
