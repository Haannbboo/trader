from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from contracts import AssetClass, Instrument, OptionRight

from apps.smoke.main import (
    build_market_adapter,
    market_bar_window,
    parse_market_instrument,
)
from config import SourceSettings


def test_parse_market_instrument_keeps_equity_symbols_simple() -> None:
    assert parse_market_instrument("AAPL", AssetClass.EQUITY) == Instrument(
        symbol="AAPL",
        asset_class=AssetClass.EQUITY,
    )


def test_parse_market_instrument_uses_occ_for_options() -> None:
    assert parse_market_instrument(
        "AAPL260612C00300000",
        AssetClass.OPTION,
    ) == Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2026, 6, 12, tzinfo=timezone.utc),
        strike=Decimal("300"),
        right=OptionRight.CALL,
    )


def test_market_bar_window_uses_fixed_market_time_window() -> None:
    start, end = market_bar_window()

    market_tz = ZoneInfo("America/New_York")
    assert start == datetime(2026, 6, 5, 10, 0, tzinfo=market_tz)
    assert end == datetime(2026, 6, 5, 11, 0, tzinfo=market_tz)


def test_build_market_adapter_discovers_configured_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discovered: list[list[str]] = []

    class FakeConfig:
        def source_params(
            self,
            domain: str,
            source: str,
            name: str | None = None,
        ) -> dict[str, Any]:
            assert (domain, source, name) == ("market", "polygon", None)
            return {"api_key": "key"}

    class FakeAdapter:
        def __init__(self, **params: Any) -> None:
            self.params = params

    def fake_discover(packages: list[str]) -> None:
        discovered.append(packages)

    def fake_get(
        domain: str, source: str, name: str | None = None
    ) -> type[FakeAdapter]:
        assert (domain, source, name) == ("market", "polygon", None)
        return FakeAdapter

    import plugins

    monkeypatch.setattr(plugins, "discover", fake_discover)
    monkeypatch.setattr(plugins.registry, "get", fake_get)

    adapter = build_market_adapter(
        FakeConfig(),  # type: ignore[arg-type]
        SourceSettings(source="polygon"),
    )

    assert discovered == [["adapters.market.polygon"]]
    assert adapter.params == {"api_key": "key"}  # type: ignore[attr-defined]
