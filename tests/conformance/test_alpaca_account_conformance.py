from __future__ import annotations

import inspect
import json
from decimal import Decimal
from importlib import import_module
from pathlib import Path

from adapters._base import BaseAccountAdapter
from contracts import EventType
from plugins import registry

ALPACA_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "alpaca"

import_module("adapters.account.alpaca")


def test_alpaca_account_adapter_declares_live_surface() -> None:
    """Alpaca exposes live alpaca-py client hooks without fixture coupling."""
    adapter_cls = registry.get("account", "alpaca")
    adapter = adapter_cls(
        api_key="key",
        api_secret="secret",
        paper=True,
        base_url="https://paper-api.alpaca.markets",
        raw_data=True,
    )

    assert adapter.api_key == "key"
    assert adapter.api_secret == "secret"
    assert adapter.paper is True
    assert adapter.base_url == "https://paper-api.alpaca.markets"
    assert adapter.raw_data is True

    for method_name in (
        "_connect",
        "_disconnect",
        "_check_health",
        "_build_trading_client",
        "_build_trading_stream",
        "_fetch_positions_raw",
        "_fetch_balance_raw",
        "_fetch_orders_raw",
        "_submit_raw",
        "_cancel_raw",
        "_subscribe_raw",
        "_to_alpaca_order_request",
        "_normalize_order",
        "_normalize_fill",
        "_normalize_position",
        "_normalize_balance",
        "_map_status",
        "_map_instrument",
        "_map_side",
        "_map_order_type",
        "_map_time_in_force",
        "_parse_timestamp",
        "_event_from_raw",
    ):
        assert hasattr(adapter, method_name), f"missing {method_name}"


def test_alpaca_account_adapter_matches_base_account_contract() -> None:
    """Concrete adapter keeps BaseAccountAdapter public/hook signatures intact."""
    adapter_cls = registry.get("account", "alpaca")

    assert "subscribe" in adapter_cls.__dict__

    for method_name in (
        "subscribe",
        "_fetch_positions_raw",
        "_fetch_balance_raw",
        "_fetch_orders_raw",
        "_submit_raw",
        "_cancel_raw",
        "_normalize_order",
        "_normalize_fill",
        "_normalize_position",
        "_normalize_balance",
        "_map_status",
    ):
        assert inspect.signature(
            getattr(adapter_cls, method_name)
        ) == inspect.signature(getattr(BaseAccountAdapter, method_name))


def test_alpaca_account_read_path_conformance_for_polling_and_streaming() -> None:
    """Alpaca read fixtures cover REST polling snapshots and stream updates."""
    adapter_cls = registry.get("account", "alpaca")
    adapter = adapter_cls(api_key="key", api_secret="secret")

    account = json.loads((ALPACA_FIXTURE_ROOT / "account.json").read_text())
    positions = json.loads((ALPACA_FIXTURE_ROOT / "positions.json").read_text())
    orders = json.loads((ALPACA_FIXTURE_ROOT / "orders.json").read_text())
    trade_update = json.loads(
        (ALPACA_FIXTURE_ROOT / "trade_update_fill.json").read_text()
    )

    balance = adapter._normalize_balance(account)
    position = adapter._normalize_position(positions[0])
    order = adapter._normalize_order(orders[0])
    event = adapter._event_from_raw(trade_update)

    assert balance.cash == Decimal("99694.41")
    assert position.instrument.symbol == "AAPL"
    assert order.status.value == "filled"
    assert event.type == EventType.FILL
    assert event.payload.price == Decimal("305.59")
