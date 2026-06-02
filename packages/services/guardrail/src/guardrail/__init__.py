"""
guardrail — mandatory middleware on the order path. AccountService.place_order
MUST call Guardrail.check(order) BEFORE any order reaches a broker. This is the
smallest, most security-critical seam in the system: it must be in place before
real money flows. Rules are simple now; the *structure* (the mandatory hop +
kill switch) is what must exist from day one.

Design notes / what to consider when adding rules:
  - A rule is pure-ish: given (order, context) -> approve / reject / clamp size.
    It must not place orders or do I/O; it only judges.
  - check() runs ALL rules; ANY rejection rejects the order. A rule may instead
    return a size-clamped order (e.g. "max 100 shares"), and later rules see the
    clamped version — order matters, so clamps compose conservatively.
  - check() NEVER silently drops an order: it returns an approved (possibly
    clamped) Order, or raises RiskRejected. The caller must not swallow that.
  - RiskContext is a live snapshot (positions, day P&L, open notional). The
    service builds it before calling check(); rules read it, never fetch it.
  - Kill switch is global and trips on operator command OR a rule can request it
    (e.g. daily-loss-limit breached). Once tripped, check() rejects everything
    until reset() — fail closed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from contracts.schema import Order, Position


class RiskRejected(Exception):
    """Raised by check() when an order is denied. Carries the reason + which rule."""

    def __init__(self, reason: str, rule: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.rule = rule


class RiskContext(BaseModel):
    """Live snapshot rules evaluate against. Built by the service per-order.
    Add fields here as rules need them (e.g. realized_day_pnl, open_notional)."""

    positions: list[Position] = []
    buying_power: Decimal = Decimal(0)
    # extend as needed: day_pnl, open_order_notional, ...


class RuleResult(BaseModel):
    approved: bool
    reason: str = ""
    clamped_order: Order | None = None  # set to request a size reduction
    request_kill: bool = False  # a rule can demand the kill switch trip


@runtime_checkable
class RiskRule(Protocol):
    """One rule = one concern. New limits scale via the registry pattern too;
    for v1 they're just constructed and passed to Guardrail."""

    name: str

    def evaluate(self, order: Order, ctx: RiskContext) -> RuleResult: ...


# --- a couple of concrete v1 rules (simple enough to write fully) -----------
class MaxQuantityRule:
    """Hard cap on per-order quantity. Clamps rather than rejects."""

    name = "max_quantity"

    def __init__(self, max_qty: Decimal) -> None:
        self._max = max_qty

    def evaluate(self, order: Order, ctx: RiskContext) -> RuleResult:
        if order.quantity <= self._max:
            return RuleResult(approved=True)
        return RuleResult(
            approved=True,
            reason=f"qty clamped to {self._max}",
            clamped_order=order.model_copy(update={"quantity": self._max}),
        )


class BuyingPowerRule:
    """Reject if a (priced) order's notional exceeds buying power. Market orders
    without a price are passed through here — notional is unknown pre-fill, so
    this rule abstains rather than guesses. (Consider a separate notional cap.)"""

    name = "buying_power"

    def evaluate(self, order: Order, ctx: RiskContext) -> RuleResult:
        price = order.limit_price
        if price is None:
            return RuleResult(approved=True)  # can't size a market order here
        if order.side.value == "buy" and price * order.quantity > ctx.buying_power:
            return RuleResult(approved=False, reason="insufficient buying power")
        return RuleResult(approved=True)


class Guardrail:
    def __init__(self, rules: list[RiskRule]) -> None:
        self._rules = rules
        self._tripped = False
        self._trip_reason = ""

    async def check(self, order: Order, ctx: RiskContext) -> Order:
        """Run all rules in order against `order`. Returns an approved (possibly
        size-clamped) Order, or raises RiskRejected. Fails closed if tripped.

        Consider: idempotency (same client_order_id re-checked), and whether a
        clamp-to-zero should become a rejection. Left explicit for the impl."""
        if self._tripped:
            raise RiskRejected(
                f"kill switch tripped: {self._trip_reason}", rule="kill_switch"
            )
        current = order
        for rule in self._rules:
            result = rule.evaluate(current, ctx)
            if result.request_kill:
                self.trip(f"{rule.name}: {result.reason}")
                raise RiskRejected(result.reason, rule=rule.name)
            if not result.approved:
                raise RiskRejected(result.reason, rule=rule.name)
            if result.clamped_order is not None:
                current = result.clamped_order
        return current

    def trip(self, reason: str) -> None:
        """Kill switch: reject everything until reset()."""
        self._tripped = True
        self._trip_reason = reason

    def reset(self) -> None:
        self._tripped = False
        self._trip_reason = ""

    @property
    def tripped(self) -> bool:
        return self._tripped
