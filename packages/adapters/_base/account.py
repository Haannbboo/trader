"""
adapters/_base/account.py — BaseAccountAdapter: shared by ACCOUNT sources only.

The richest domain base, because all brokers share real logic: client_order_id
idempotency, order-state mapping, fill stream wrapping. A concrete broker fills
only: how to talk to ITS API (_submit_raw / _cancel_raw / _fetch_*), and the
_normalize_* hooks. The idempotency + event-wrapping flow is written once here.

Structurally satisfies AccountSourcePort. NOTE: hard risk limits live in the
guardrail at the SERVICE layer; this base only does broker-protocol commonality.
"""
from __future__ import annotations

from typing import AsyncIterator
from contracts import (
    Balance,
    Event,
    Fill,
    Order,
    OrderStatus,
    Position,
    AccountSourcePort,
)
from adapters._base.base import BaseAdapter


class BaseAccountAdapter(BaseAdapter, AccountSourcePort):
    """Base class for account and execution adapters."""

    # --- reads: common flow -> normalize hook ---
    async def get_positions(self) -> list[Position]:
        """Common: limiter -> _fetch_positions_raw -> map _normalize_position."""
        raise NotImplementedError()

    async def get_balance(self) -> Balance:
        """Common: limiter -> _fetch_balance_raw -> _normalize_balance."""
        raise NotImplementedError()

    async def get_orders(self) -> list[Order]:
        """Common: limiter -> _fetch_orders_raw -> map _normalize_order."""
        raise NotImplementedError()

    # --- writes: idempotency lives here, once, for all brokers ---
    async def place_order(self, order: Order) -> Order:
        """Common: ensure client_order_id (idempotency key); skip/replay if we've
        already sent this id; else _submit_raw -> _normalize_order. Concrete
        brokers never reimplement idempotency."""
        raise NotImplementedError()

    async def cancel_order(self, broker_order_id: str) -> None:
        """Common: limiter -> _cancel_raw."""
        raise NotImplementedError()

    # --- stream: fills + order/position updates as a uniform Event stream ---
    async def subscribe(self) -> AsyncIterator[Event]:
        """Common: open the upstream account stream (push or polled), route each
        raw item through the right _normalize_* and wrap as Event[Fill|Order|
        Position|Balance]."""
        if False:
            yield
        raise NotImplementedError()

    # --- hooks a concrete broker fills ---
    async def _fetch_positions_raw(self) -> list[dict]:
        """Fetch raw positions from the broker's API."""
        raise NotImplementedError()

    async def _fetch_balance_raw(self) -> dict:
        """Fetch raw balance information from the broker's API."""
        raise NotImplementedError()

    async def _fetch_orders_raw(self) -> list[dict]:
        """Fetch raw orders from the broker's API."""
        raise NotImplementedError()

    async def _submit_raw(self, order: Order) -> dict:
        """Send to the broker; return the broker's raw ack."""
        raise NotImplementedError()

    async def _cancel_raw(self, broker_order_id: str) -> None:
        """Cancel the order via the broker's API."""
        raise NotImplementedError()

    def _normalize_order(self, raw: dict) -> Order:
        """Normalize raw broker order JSON into an Order contract."""
        raise NotImplementedError()

    def _normalize_fill(self, raw: dict) -> Fill:
        """Normalize raw broker fill JSON into a Fill contract."""
        raise NotImplementedError()

    def _normalize_position(self, raw: dict) -> Position:
        """Normalize raw broker position JSON into a Position contract."""
        raise NotImplementedError()

    def _normalize_balance(self, raw: dict) -> Balance:
        """Normalize raw broker balance JSON into a Balance contract."""
        raise NotImplementedError()

    def _map_status(self, raw_status: str) -> OrderStatus:
        """Common helper: every broker's status vocabulary -> our OrderStatus."""
        raise NotImplementedError()
