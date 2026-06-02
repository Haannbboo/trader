from __future__ import annotations

from typing import Protocol, runtime_checkable
from pydantic import BaseModel
from contracts.schema import Order


class RiskRejected(Exception):
    """Raised when an order fails risk evaluation checks."""

    pass


class RiskContext(BaseModel):
    """Live snapshot the rules evaluate against (positions, notional, day P&L...)."""

    pass


class RuleResult(BaseModel):
    approved: bool
    reason: str = ""
    clamped_order: Order | None = None  # e.g. size reduced to a cap


@runtime_checkable
class RiskRule(Protocol):
    """One rule = one package/file; new limits scale via the registry pattern too."""

    name: str

    def evaluate(self, order: Order, ctx: RiskContext) -> RuleResult: ...


class Guardrail:
    """Mandatory middleware on the order path. AccountService.place_order MUST route through check()."""

    def __init__(self, rules: list[RiskRule]) -> None:
        """Initialize Guardrail with a set of RiskRules."""
        self._tripped = False

    async def check(self, order: Order) -> Order:
        """Run all rules. Return an approved (possibly size-clamped) order, or
        raise RiskRejected. NEVER silently drops an order.
        """
        raise NotImplementedError()

    def trip(self, reason: str) -> None:
        """Kill switch: reject everything until reset."""
        raise NotImplementedError()

    def reset(self) -> None:
        """Deactivate the global kill switch."""
        raise NotImplementedError()

    @property
    def tripped(self) -> bool:
        """Get the active state of the global kill switch."""
        raise NotImplementedError()
