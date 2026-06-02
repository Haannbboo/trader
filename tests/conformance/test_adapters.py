from importlib import import_module

import pytest
from plugins import registry
from contracts import AccountSourcePort, MarketSourcePort, NewsSourcePort

# Trigger imports to register plugins
for module_name in (
    "adapters.market.ibkr",
    "adapters.market.polygon",
    "adapters.news.benzinga",
    "adapters.news.rss",
    "adapters.account.ibkr",
    "adapters.account.alpaca",
):
    import_module(module_name)


@pytest.mark.parametrize("name", registry.names("market"))
def test_market_adapters_conformance(name: str) -> None:
    """Verifies that every registered market adapter complies with the MarketSourcePort Protocol."""
    adapter_cls = registry.get("market", name)
    adapter = adapter_cls()

    assert hasattr(adapter, "start"), f"{name} must implement start"
    assert hasattr(adapter, "stop"), f"{name} must implement stop"
    assert hasattr(adapter, "health"), f"{name} must implement health"
    assert hasattr(adapter, "get_quote"), f"{name} must implement get_quote"
    assert hasattr(adapter, "get_bars"), f"{name} must implement get_bars"
    assert hasattr(adapter, "subscribe"), f"{name} must implement subscribe"

    assert not adapter.connected
    assert isinstance(adapter, MarketSourcePort)


@pytest.mark.parametrize("name", registry.names("news"))
def test_news_adapters_conformance(name: str) -> None:
    """Verifies that every registered news adapter complies with the NewsSourcePort Protocol."""
    adapter_cls = registry.get("news", name)
    adapter = adapter_cls()

    assert hasattr(adapter, "start"), f"{name} must implement start"
    assert hasattr(adapter, "stop"), f"{name} must implement stop"
    assert hasattr(adapter, "health"), f"{name} must implement health"
    assert hasattr(adapter, "query"), f"{name} must implement query"
    assert hasattr(adapter, "subscribe"), f"{name} must implement subscribe"

    assert isinstance(adapter, NewsSourcePort)


@pytest.mark.parametrize("name", registry.names("account"))
def test_account_adapters_conformance(name: str) -> None:
    """Verifies that every registered account adapter complies with the AccountSourcePort Protocol."""
    adapter_cls = registry.get("account", name)
    adapter = adapter_cls()

    assert hasattr(adapter, "start"), f"{name} must implement start"
    assert hasattr(adapter, "stop"), f"{name} must implement stop"
    assert hasattr(adapter, "health"), f"{name} must implement health"
    assert hasattr(adapter, "get_positions"), f"{name} must implement get_positions"
    assert hasattr(adapter, "get_balance"), f"{name} must implement get_balance"
    assert hasattr(adapter, "get_orders"), f"{name} must implement get_orders"
    assert hasattr(adapter, "place_order"), f"{name} must implement place_order"
    assert hasattr(adapter, "cancel_order"), f"{name} must implement cancel_order"
    assert hasattr(adapter, "subscribe"), f"{name} must implement subscribe"

    assert isinstance(adapter, AccountSourcePort)
