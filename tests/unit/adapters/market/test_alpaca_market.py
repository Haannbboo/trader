"""Unit tests for the Alpaca market adapters (stock + option).

These tests cover the read-side of the contract: get_bars normalization,
streaming subscribe() over the alpaca-py data streams, OCC symbol derivation
for options, and the lazy import boundary that lets registry discovery work
without the `alpaca` extra installed.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Coroutine

import pytest
from adapters.market.alpaca import (
    AlpacaCryptoMarketAdapter,
    AlpacaOptionMarketAdapter,
    AlpacaStockMarketAdapter,
)
from contracts import (
    AssetClass,
    Bar,
    EventType,
    Instrument,
    MarketChannel,
    OptionRight,
    Timeframe,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "alpaca"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURE_ROOT / name).read_text())


# ---------------------------------------------------------------------------
# Fakes: narrow alpaca-py surfaces the adapters depend on.
# ---------------------------------------------------------------------------
class FakeStockHistoricalClient:
    def __init__(self) -> None:
        self.bars_payload = load_fixture("stock_bars.json")
        self.quote_payload = load_fixture("stock_quote.json")
        self.last_request: Any = None

    def get_stock_bars(self, request: Any) -> Any:
        self.last_request = request
        return {"AAPL": self.bars_payload["bars"]}

    def get_stock_latest_quote(self, request: Any) -> Any:
        self.last_request = request
        return {"AAPL": self.quote_payload}


class FakeOptionHistoricalClient:
    def __init__(self) -> None:
        self.bars_payload = load_fixture("option_bars.json")
        self.quote_payload = load_fixture("option_quote.json")
        self.last_request: Any = None

    def get_option_bars(self, request: Any) -> Any:
        self.last_request = request
        return {"AAPL240119C00150000": self.bars_payload["bars"]}

    def get_option_latest_quote(self, request: Any) -> Any:
        self.last_request = request
        return {"AAPL240119C00150000": self.quote_payload}


class FakeStockHistoricalDtoClient:
    def __init__(self, bar: Bar) -> None:
        self.bar = bar
        self.last_request: Any = None

    def get_stock_bars(self, request: Any) -> Any:
        self.last_request = request
        return {"AAPL": [self.bar]}


class FakeStockDataStream:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}
        self.subscribed_symbols: dict[str, list[str]] = {}
        self.stopped = False
        self.quote = load_fixture("stock_quote.json")
        self.trade = load_fixture("stock_trade.json")
        self.bar = load_fixture("stock_bars.json")["bars"][0]

    def subscribe_trades(
        self, handler: Callable[..., Coroutine[Any, Any, None]], *symbols: str
    ) -> None:
        self.handlers["trades"] = handler
        self.subscribed_symbols.setdefault("trades", []).extend(symbols)

    def subscribe_quotes(
        self, handler: Callable[..., Coroutine[Any, Any, None]], *symbols: str
    ) -> None:
        self.handlers["quotes"] = handler
        self.subscribed_symbols.setdefault("quotes", []).extend(symbols)

    def subscribe_bars(
        self, handler: Callable[..., Coroutine[Any, Any, None]], *symbols: str
    ) -> None:
        self.handlers["bars"] = handler
        self.subscribed_symbols.setdefault("bars", []).extend(symbols)

    def run(self) -> None:
        assert self.handlers, "no subscriptions registered"
        for channel, handler in self.handlers.items():
            payload = {
                "trades": self.trade,
                "quotes": self.quote,
                "bars": self.bar,
            }[channel]
            asyncio.run(handler(payload))

    def stop(self) -> None:
        self.stopped = True


class FakeBrokenStockHistoricalClient:
    def get_stock_bars(self, request: Any) -> Any:
        raise AttributeError("internal SDK bug")

    def get_option_bars(self, request: Any) -> Any:
        raise AssertionError("get_option_bars must not be called by stock adapter")


class FakeOptionDataStream(FakeStockDataStream):
    def __init__(self) -> None:
        super().__init__()
        self.quote = load_fixture("option_quote.json")
        self.trade = load_fixture("option_trade.json")
        self.bar = load_fixture("option_bars.json")["bars"][0]


# ---------------------------------------------------------------------------
# Fakes: crypto surfaces for the AlpacaCryptoMarketAdapter tests.
# ---------------------------------------------------------------------------
class FakeCryptoHistoricalClient:
    """Narrow stand-in for alpaca.data.historical.crypto.CryptoHistoricalDataClient.

    Mirrors the pattern of FakeStockHistoricalClient: payload attributes are
    instance-level (not copied) so tests can mutate ``bars_payload`` after
    construction. The zero-value test depends on this mutability.
    """

    def __init__(self) -> None:
        self.bars_payload = load_fixture("crypto_bars.json")
        self.quote_payload = load_fixture("crypto_quote.json")
        self.last_request: Any = None

    def get_crypto_bars(self, request: Any) -> Any:
        self.last_request = request
        return {"BTC/USD": self.bars_payload["bars"]}

    def get_crypto_latest_quote(self, request: Any) -> Any:
        self.last_request = request
        return {"BTC/USD": self.quote_payload}


class FakeCryptoDataStream:
    """Opaque marker — ``_connect`` only stores the injected stream.

    No methods are needed: the pull path never calls subscribe_*/run/stop on
    the data stream. Tests that exercise ``_build_data_stream`` directly use
    monkeypatch instead of this fake.
    """


# ---------------------------------------------------------------------------
# Stock adapter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_alpaca_stock_get_bars_normalizes_fixture_to_schema_bars() -> None:
    client = FakeStockHistoricalClient()
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    bars = await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert len(bars) == 2
    assert bars[0].instrument.symbol == "AAPL"
    assert bars[0].timeframe == Timeframe.M1
    assert bars[0].open == Decimal("150.10")
    assert bars[0].high == Decimal("150.80")
    assert bars[0].low == Decimal("149.95")
    assert bars[0].close == Decimal("150.55")
    assert bars[0].volume == Decimal("12345")
    assert bars[0].vwap == Decimal("150.40")
    assert bars[0].trades == 87
    assert bars[0].ts_open == datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    assert bars[1].close == Decimal("151.00")
    assert client.last_request.symbol_or_symbols == "AAPL"
    assert client.last_request.timeframe.amount == 1
    # alpaca-py's TimeFrameUnit.Minute serializes as "Min" in its enum value;
    # the real source of truth is the TimeFrame object itself, which we built
    # via _alpaca_timeframe(Timeframe.M1).
    assert client.last_request.timeframe.unit.name == "Minute"
    # Default feed must be SIP (per adapter contract), not the alpaca-py default.
    assert client.last_request.feed.name == "SIP"


@pytest.mark.asyncio
async def test_alpaca_stock_get_bars_preserves_zero_values() -> None:
    client = FakeStockHistoricalClient()
    client.bars_payload = {
        "bars": [
            {
                "t": "2024-01-19T14:30:00Z",
                "o": 0,
                "h": 0,
                "l": 0,
                "c": 0,
                "v": 0,
                "vw": 0,
                "n": 0,
            }
        ]
    }
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    bars = await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert bars[0].open == Decimal("0")
    assert bars[0].high == Decimal("0")
    assert bars[0].low == Decimal("0")
    assert bars[0].close == Decimal("0")
    assert bars[0].volume == Decimal("0")
    assert bars[0].vwap == Decimal("0")
    assert bars[0].trades == 0


@pytest.mark.asyncio
async def test_alpaca_stock_get_bars_accepts_bar_dto_payloads() -> None:
    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    normalized_bar = Bar(
        instrument=instrument,
        timeframe=Timeframe.M1,
        ts_open=datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc),
        open=Decimal("150.10"),
        high=Decimal("150.80"),
        low=Decimal("149.95"),
        close=Decimal("150.55"),
        volume=Decimal("12345"),
        vwap=Decimal("150.40"),
        trades=87,
    )
    client = FakeStockHistoricalDtoClient(normalized_bar)
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    bars = await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert bars == [normalized_bar]


def test_alpaca_stock_feed_defaults_to_sip() -> None:
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeStockHistoricalClient(),
    )
    assert adapter.feed == "sip"


def test_alpaca_stock_feed_can_be_iex() -> None:
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeStockHistoricalClient(),
        feed="iex",
    )
    assert adapter.feed == "iex"


def test_alpaca_stock_feed_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="feed"):
        AlpacaStockMarketAdapter(
            api_key="key",
            api_secret="secret",
            historical_client=FakeStockHistoricalClient(),
            feed="otc",
        )


@pytest.mark.asyncio
async def test_alpaca_stock_get_bars_passes_iex_feed_to_request() -> None:
    client = FakeStockHistoricalClient()
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
        feed="iex",
    )
    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert client.last_request.feed.name == "IEX"


@pytest.mark.asyncio
async def test_alpaca_stock_rejects_option_instrument_in_get_bars() -> None:
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeStockHistoricalClient(),
    )
    option = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2024, 1, 19, tzinfo=timezone.utc),
        strike=Decimal("150"),
        right=OptionRight.CALL,
    )

    with pytest.raises(ValueError, match="equity"):
        await adapter.get_bars(
            option,
            Timeframe.M1,
            datetime(2024, 1, 19, tzinfo=timezone.utc),
            datetime(2024, 1, 19, 1, 0, tzinfo=timezone.utc),
        )


def test_alpaca_stock_rejects_option_instrument_in_subscribe() -> None:
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeStockHistoricalClient(),
        data_stream=FakeStockDataStream(),
    )
    option = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2024, 1, 19, tzinfo=timezone.utc),
        strike=Decimal("150"),
        right=OptionRight.CALL,
    )

    with pytest.raises(ValueError, match="equity"):
        list(adapter.subscribe([option], [MarketChannel.TRADES]))


@pytest.mark.asyncio
async def test_alpaca_stock_subscribe_trades_emits_quote_events() -> None:
    stream = FakeStockDataStream()
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeStockHistoricalClient(),
        data_stream=stream,
    )
    instruments = [Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)]

    agen = adapter.subscribe(instruments, [MarketChannel.TRADES])
    event = await anext(agen)
    await agen.aclose()

    assert event.type == EventType.QUOTE
    assert event.source == "AlpacaStockMarketAdapter"
    assert event.payload.instrument.symbol == "AAPL"
    assert event.payload.last == Decimal("150.50")
    assert event.payload.last_size == Decimal("25")
    assert stream.subscribed_symbols["trades"] == ["AAPL"]


@pytest.mark.asyncio
async def test_alpaca_stock_subscribe_quotes_emits_quote_events() -> None:
    stream = FakeStockDataStream()
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeStockHistoricalClient(),
        data_stream=stream,
    )
    instruments = [Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)]

    agen = adapter.subscribe(instruments, [MarketChannel.QUOTES])
    event = await anext(agen)
    await agen.aclose()

    assert event.type == EventType.QUOTE
    assert event.payload.bid == Decimal("150.45")
    assert event.payload.ask == Decimal("150.50")
    assert stream.subscribed_symbols["quotes"] == ["AAPL"]


@pytest.mark.asyncio
async def test_alpaca_stock_subscribe_bars_emits_bar_events() -> None:
    stream = FakeStockDataStream()
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeStockHistoricalClient(),
        data_stream=stream,
    )
    instruments = [Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)]

    agen = adapter.subscribe(instruments, [MarketChannel.BARS])
    event = await anext(agen)
    await agen.aclose()

    assert event.type == EventType.BAR
    assert event.payload.instrument.symbol == "AAPL"
    assert event.payload.close == Decimal("150.55")
    assert stream.subscribed_symbols["bars"] == ["AAPL"]


def test_alpaca_stock_caps_capabilities_at_equity() -> None:
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeStockHistoricalClient(),
    )

    assert AssetClass.EQUITY in adapter.capabilities.asset_classes
    assert AssetClass.OPTION not in adapter.capabilities.asset_classes
    assert adapter.capabilities.supports_streaming is True
    assert adapter.capabilities.historical is True


@pytest.mark.asyncio
async def test_alpaca_stock_get_bars_does_not_fallback_on_internal_attribute_error() -> (
    None
):
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeBrokenStockHistoricalClient(),
    )
    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    with pytest.raises(AttributeError, match="internal SDK bug"):
        await adapter.get_bars(instrument, Timeframe.M1, start, end)


# ---------------------------------------------------------------------------
# Option adapter
# ---------------------------------------------------------------------------
def test_alpaca_option_feed_defaults_to_opra() -> None:
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeOptionHistoricalClient(),
    )
    assert adapter.feed == "opra"


def test_alpaca_option_feed_can_be_indicative() -> None:
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeOptionHistoricalClient(),
        feed="indicative",
    )
    assert adapter.feed == "indicative"


def test_alpaca_option_feed_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="feed"):
        AlpacaOptionMarketAdapter(
            api_key="key",
            api_secret="secret",
            historical_client=FakeOptionHistoricalClient(),
            feed="iex",
        )


@pytest.mark.asyncio
async def test_alpaca_option_get_bars_uses_occ_symbol_and_normalizes() -> None:
    client = FakeOptionHistoricalClient()
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    instrument = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2024, 1, 19, tzinfo=timezone.utc),
        strike=Decimal("150"),
        right=OptionRight.CALL,
    )
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    bars = await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert len(bars) == 2
    assert bars[0].instrument.symbol == "AAPL"
    assert bars[0].instrument.asset_class == AssetClass.OPTION
    assert bars[0].close == Decimal("5.25")
    assert client.last_request.symbol_or_symbols == "AAPL240119C00150000"


@pytest.mark.asyncio
async def test_alpaca_option_get_bars_does_not_send_feed_to_request() -> None:
    client = FakeOptionHistoricalClient()
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
        feed="indicative",
    )
    instrument = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2024, 1, 19, tzinfo=timezone.utc),
        strike=Decimal("150"),
        right=OptionRight.CALL,
    )
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert "feed" not in client.last_request.to_request_fields()


def test_alpaca_option_data_stream_uses_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeOptionDataStreamCtor:
        def __init__(
            self,
            *,
            api_key: str,
            secret_key: str,
            feed: Any,
        ) -> None:
            captured["api_key"] = api_key
            captured["secret_key"] = secret_key
            captured["feed"] = feed

    import alpaca.data.live.option as option_live

    monkeypatch.setattr(option_live, "OptionDataStream", FakeOptionDataStreamCtor)
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        feed="indicative",
    )

    adapter._build_data_stream()

    assert captured["api_key"] == "key"
    assert captured["secret_key"] == "secret"
    assert captured["feed"].value == "indicative"


@pytest.mark.asyncio
async def test_alpaca_option_subscribe_trades_emits_quote_events() -> None:
    stream = FakeOptionDataStream()
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeOptionHistoricalClient(),
        data_stream=stream,
    )
    instrument = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2024, 1, 19, tzinfo=timezone.utc),
        strike=Decimal("150"),
        right=OptionRight.CALL,
    )

    agen = adapter.subscribe([instrument], [MarketChannel.TRADES])
    event = await anext(agen)
    await agen.aclose()

    assert event.type == EventType.QUOTE
    assert event.payload.last == Decimal("5.25")
    assert stream.subscribed_symbols["trades"] == ["AAPL240119C00150000"]


def test_alpaca_option_rejects_equity_instrument() -> None:
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeOptionHistoricalClient(),
    )
    equity = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)

    with pytest.raises(ValueError, match="option"):
        list(adapter.subscribe([equity], [MarketChannel.TRADES]))


def test_alpaca_option_caps_capabilities_at_option() -> None:
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=FakeOptionHistoricalClient(),
    )

    assert AssetClass.OPTION in adapter.capabilities.asset_classes
    assert AssetClass.EQUITY not in adapter.capabilities.asset_classes


# ---------------------------------------------------------------------------
# Crypto adapter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_alpaca_crypto_get_bars_normalizes_fixture_to_schema_bars() -> None:
    client = FakeCryptoHistoricalClient()
    adapter = AlpacaCryptoMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    instrument = Instrument(symbol="BTC/USD", asset_class=AssetClass.CRYPTO)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    bars = await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert len(bars) == 2
    assert bars[0].instrument.symbol == "BTC/USD"
    assert bars[0].instrument.asset_class == AssetClass.CRYPTO
    assert bars[0].timeframe == Timeframe.M1
    assert bars[0].open == Decimal("42000.10")
    assert bars[0].high == Decimal("42100.80")
    assert bars[0].low == Decimal("41900.95")
    assert bars[0].close == Decimal("42050.55")
    assert bars[0].volume == Decimal("12.5")
    assert bars[0].vwap == Decimal("42040.40")
    assert bars[0].trades == 87
    assert bars[0].ts_open == datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    assert bars[1].close == Decimal("42100.00")
    assert client.last_request.symbol_or_symbols == "BTC/USD"
    assert client.last_request.timeframe.amount == 1
    assert client.last_request.timeframe.unit.name == "Minute"
    # CryptoBarsRequest does not accept a feed kwarg; mirror the option test.
    assert "feed" not in client.last_request.to_request_fields()


@pytest.mark.asyncio
async def test_alpaca_crypto_get_bars_preserves_zero_values() -> None:
    """Regression test: zero prices/volumes/trade counts must not be coerced to None.

    Depends on FakeCryptoHistoricalClient.bars_payload being a mutable attribute
    (not a copy in __init__); we mutate it here to feed in zero-valued bars.
    """
    client = FakeCryptoHistoricalClient()
    client.bars_payload = {
        "bars": [
            {
                "t": "2024-01-19T14:30:00Z",
                "o": 0,
                "h": 0,
                "l": 0,
                "c": 0,
                "v": 0,
                "vw": 0,
                "n": 0,
            }
        ]
    }
    adapter = AlpacaCryptoMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    instrument = Instrument(symbol="BTC/USD", asset_class=AssetClass.CRYPTO)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    bars = await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert len(bars) == 1
    assert bars[0].open == Decimal("0")
    assert bars[0].high == Decimal("0")
    assert bars[0].low == Decimal("0")
    assert bars[0].close == Decimal("0")
    assert bars[0].volume == Decimal("0")
    assert bars[0].vwap == Decimal("0")
    assert bars[0].trades == 0


@pytest.mark.asyncio
async def test_alpaca_crypto_get_quote_normalizes_fixture_to_schema_quote() -> None:
    client = FakeCryptoHistoricalClient()
    adapter = AlpacaCryptoMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    instrument = Instrument(symbol="BTC/USD", asset_class=AssetClass.CRYPTO)

    quote = await adapter.get_quote(instrument)

    assert quote.instrument.symbol == "BTC/USD"
    assert quote.instrument.asset_class == AssetClass.CRYPTO
    assert quote.bid == Decimal("42000.45")
    assert quote.ask == Decimal("42000.50")
    assert quote.bid_size == Decimal("1.5")
    assert quote.ask_size == Decimal("2.0")
    assert quote.last == Decimal("42000.48")
    assert quote.last_size == Decimal("0.5")
    assert quote.ts_event == datetime(
        2024, 1, 19, 14, 30, 0, 123456, tzinfo=timezone.utc
    )
    assert client.last_request.symbol_or_symbols == "BTC/USD"


@pytest.mark.asyncio
async def test_alpaca_crypto_rejects_non_crypto_instrument() -> None:
    """An EQUITY or OPTION instrument must raise ValueError from _assert_supported."""
    client = FakeCryptoHistoricalClient()
    adapter = AlpacaCryptoMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    equity = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="only supports crypto"):
        await adapter.get_bars(equity, Timeframe.M1, start, end)


@pytest.mark.asyncio
async def test_alpaca_crypto_rejects_malformed_symbol() -> None:
    """A symbol without a '/' slash is rejected at _native_symbol."""
    client = FakeCryptoHistoricalClient()
    adapter = AlpacaCryptoMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    bad = Instrument(symbol="BTCUSD", asset_class=AssetClass.CRYPTO)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="BASE/QUOTE"):
        await adapter.get_bars(bad, Timeframe.M1, start, end)


@pytest.mark.asyncio
async def test_alpaca_crypto_passes_symbol_through_to_native() -> None:
    """The symbol 'BTC/USD' must reach the SDK request unchanged (no split/reformat)."""
    client = FakeCryptoHistoricalClient()
    adapter = AlpacaCryptoMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    instrument = Instrument(symbol="ETH/USDC", asset_class=AssetClass.CRYPTO)
    start = datetime(2024, 1, 19, 14, 30, tzinfo=timezone.utc)
    end = datetime(2024, 1, 19, 15, 0, tzinfo=timezone.utc)

    await adapter.get_bars(instrument, Timeframe.M1, start, end)

    assert client.last_request.symbol_or_symbols == "ETH/USDC"


@pytest.mark.asyncio
async def test_alpaca_crypto_get_quote_swallows_unsupported_feed_kwarg() -> None:
    """Pins the one behavioural divergence from stock/option: the feed kwarg
    is accepted but not validated against a per-adapter allow-list. This
    matches alpaca-py's single-CryptoFeed reality. A future refactor that
    re-introduces validation will break this test.
    """
    client = FakeCryptoHistoricalClient()
    adapter = AlpacaCryptoMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
    )
    instrument = Instrument(symbol="BTC/USD", asset_class=AssetClass.CRYPTO)

    quote = await adapter.get_quote(instrument, feed="opra")

    assert quote.instrument.symbol == "BTC/USD"
    assert quote.bid == Decimal("42000.45")


def test_alpaca_crypto_is_registered_under_market_alpaca_crypto() -> None:
    from plugins import registry

    crypto_cls = registry.get("market", "alpaca", "crypto")
    assert crypto_cls is AlpacaCryptoMarketAdapter


def test_alpaca_crypto_data_stream_constructs_crypto_data_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a typo in the lazy import path for CryptoDataStream.

    Mirrors test_alpaca_option_data_stream_uses_feed (line ~515 of this file).
    """
    captured: dict[str, Any] = {}

    class FakeCryptoDataStreamCtor:
        def __init__(self, *, api_key: str, secret_key: str) -> None:
            captured["api_key"] = api_key
            captured["secret_key"] = secret_key

    import alpaca.data.live.crypto as crypto_live

    monkeypatch.setattr(crypto_live, "CryptoDataStream", FakeCryptoDataStreamCtor)
    adapter = AlpacaCryptoMarketAdapter(
        api_key="key",
        api_secret="secret",
    )

    adapter._build_data_stream()

    assert captured["api_key"] == "key"
    assert captured["secret_key"] == "secret"


# ---------------------------------------------------------------------------
# Lazy import + registry discovery
# ---------------------------------------------------------------------------
def test_alpaca_adapters_register_under_distinct_market_names() -> None:
    from plugins import registry

    stock_cls = registry.get("market", "alpaca", "stock")
    option_cls = registry.get("market", "alpaca", "option")
    crypto_cls = registry.get("market", "alpaca", "crypto")
    assert stock_cls is AlpacaStockMarketAdapter
    assert option_cls is AlpacaOptionMarketAdapter
    assert crypto_cls is AlpacaCryptoMarketAdapter


def test_alpaca_module_does_not_import_alpaca_py_at_import_time() -> None:
    """Importing the adapter module must NOT pull alpaca-py into sys.modules.

    Registry discovery imports every adapter module by name; the project
    stays importable (and discovery stays working) when the `alpaca` extra
    is not installed only if alpaca-py imports stay deferred to the
    methods that actually need them.
    """
    alpaca_modules_before = {
        name for name in sys.modules if name == "alpaca" or name.startswith("alpaca.")
    }
    try:
        # Drop the adapter from sys.modules so the next import is a fresh load.
        sys.modules.pop("adapters.market.alpaca", None)
        importlib.import_module("adapters.market.alpaca")
        alpaca_modules_after = {
            name
            for name in sys.modules
            if name == "alpaca" or name.startswith("alpaca.")
        }
        assert alpaca_modules_after == alpaca_modules_before, (
            f"adapters.market.alpaca imported alpaca-py at module load: "
            f"{alpaca_modules_after - alpaca_modules_before}"
        )
    finally:
        sys.modules.pop("adapters.market.alpaca", None)


def test_alpaca_adapter_construction_does_not_import_alpaca_py() -> None:
    """Construction must also stay SDK-free; only the lazy hooks touch alpaca."""
    alpaca_modules_before = {
        name for name in sys.modules if name == "alpaca" or name.startswith("alpaca.")
    }
    try:
        stock = AlpacaStockMarketAdapter(
            api_key="k",
            api_secret="s",
            historical_client=FakeStockHistoricalClient(),
        )
        option = AlpacaOptionMarketAdapter(
            api_key="k",
            api_secret="s",
            historical_client=FakeOptionHistoricalClient(),
        )
        crypto = AlpacaCryptoMarketAdapter(
            api_key="k",
            api_secret="s",
            historical_client=FakeCryptoHistoricalClient(),
        )
        alpaca_modules_after = {
            name
            for name in sys.modules
            if name == "alpaca" or name.startswith("alpaca.")
        }
        assert alpaca_modules_after == alpaca_modules_before
        assert stock.historical_client is not None
        assert option.historical_client is not None
        assert crypto.historical_client is not None
    finally:
        pass


@pytest.mark.asyncio
async def test_alpaca_stock_get_quote_normalizes_fixture() -> None:
    client = FakeStockHistoricalClient()
    adapter = AlpacaStockMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
        feed="sip",
    )
    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)

    # 1. Default feed from constructor ("sip")
    quote = await adapter.get_quote(instrument)
    assert quote.instrument.symbol == "AAPL"
    assert quote.bid == Decimal("150.45")
    assert quote.ask == Decimal("150.50")
    assert quote.bid_size == Decimal("100")
    assert quote.ask_size == Decimal("200")
    assert quote.ts_event == datetime(
        2024, 1, 19, 14, 30, 0, 123456, tzinfo=timezone.utc
    )
    assert client.last_request.symbol_or_symbols == "AAPL"
    assert client.last_request.feed.name == "SIP"

    # 2. Feed override parameter ("delayed_sip")
    await adapter.get_quote(instrument, feed="delayed_sip")
    assert client.last_request.feed.name == "DELAYED_SIP"

    # 3. Invalid feed parameter
    with pytest.raises(ValueError, match="feed must be one of"):
        await adapter.get_quote(instrument, feed="invalid_feed")


@pytest.mark.asyncio
async def test_alpaca_option_get_quote_normalizes_fixture() -> None:
    client = FakeOptionHistoricalClient()
    adapter = AlpacaOptionMarketAdapter(
        api_key="key",
        api_secret="secret",
        historical_client=client,
        feed="opra",
    )
    instrument = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        strike=Decimal("150.00"),
        right=OptionRight.CALL,
        expiry=datetime(2024, 1, 19, tzinfo=timezone.utc),
    )

    # 1. Default feed from constructor ("opra")
    quote = await adapter.get_quote(instrument)
    assert quote.instrument.symbol == "AAPL"
    assert quote.bid == Decimal("5.20")
    assert quote.ask == Decimal("5.30")
    assert quote.bid_size == Decimal("50")
    assert quote.ask_size == Decimal("75")
    assert quote.ts_event == datetime(
        2024, 1, 19, 14, 30, 0, 123456, tzinfo=timezone.utc
    )
    assert client.last_request.symbol_or_symbols == "AAPL240119C00150000"
    assert client.last_request.feed.name == "OPRA"

    # 2. Feed override parameter ("indicative")
    await adapter.get_quote(instrument, feed="indicative")
    assert client.last_request.feed.name == "INDICATIVE"

    # 3. Invalid feed parameter
    with pytest.raises(ValueError, match="feed must be one of"):
        await adapter.get_quote(instrument, feed="invalid_feed")
