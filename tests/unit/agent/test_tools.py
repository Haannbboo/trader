from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import pytest
from contracts import (
    AssetClass,
    Balance,
    Bar,
    FeatureValue,
    Instrument,
    NewsFilter,
    NewsItem,
    Order,
    OrderFilter,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
    Timeframe,
    TimeInForce,
)
from tools import ToolLayer


class MockAccountService:
    def __init__(self) -> None:
        self.placed_order = None
        self.cancelled_broker_order_id = None
        self.get_orders_status: OrderFilter | None = None
        self.get_orders_symbols: list[str] | None = None

    async def get_balance(self) -> Balance:
        return Balance(
            cash=Decimal("10000"),
            equity=Decimal("15000"),
            buying_power=Decimal("20000"),
            ts_event=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
        )

    async def get_positions(self) -> list[Position]:
        return [
            Position(
                instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
                quantity=Decimal("10"),
                avg_price=Decimal("150"),
                ts_event=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
            )
        ]

    async def get_orders(
        self,
        *,
        status: OrderFilter = OrderFilter.OPEN,
        symbols: list[str] | None = None,
    ) -> list[Order]:
        self.get_orders_status = status
        self.get_orders_symbols = list(symbols) if symbols else None
        return [
            Order(
                client_order_id="client-1",
                instrument=Instrument(
                    symbol=symbols[0] if symbols else "AAPL",
                    asset_class=AssetClass.EQUITY,
                ),
                side=Side.BUY,
                quantity=Decimal("5"),
                order_type=OrderType.MARKET,
                tif=TimeInForce.DAY,
                broker_order_id="broker-789",
                status=OrderStatus.NEW,
            )
        ]

    async def place_order(self, order: Order) -> Order:
        self.placed_order = order
        return order.model_copy(
            update={
                "broker_order_id": "broker-456",
                "status": OrderStatus.PENDING_NEW,
            }
        )

    async def cancel_order(self, broker_order_id: str) -> None:
        self.cancelled_broker_order_id = broker_order_id


class MockMarketService:
    def __init__(self) -> None:
        self.instrument_quote = None
        self.bars_args = None

    async def get_quote(self, instrument: Instrument) -> Quote:
        self.instrument_quote = instrument
        return Quote(
            instrument=instrument,
            bid=Decimal("180.50"),
            bid_size=Decimal("100"),
            ask=Decimal("180.60"),
            ask_size=Decimal("200"),
            ts_event=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
        )

    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        self.bars_args = (instrument, timeframe, start, end)
        return [
            Bar(
                instrument=instrument,
                timeframe=timeframe,
                open=Decimal("180.00"),
                high=Decimal("181.00"),
                low=Decimal("179.50"),
                close=Decimal("180.50"),
                volume=Decimal("1000"),
                ts_open=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
            )
        ]


class MockNewsService:
    def __init__(self) -> None:
        self.filter = None

    async def query(self, flt: NewsFilter) -> list[NewsItem]:
        self.filter = flt
        return [
            NewsItem(
                id="news-1",
                source="mock_news",
                headline="AAPL stock rises",
                body="Apple Inc stock price rose on Tuesday.",
                published_at=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
            )
        ]


class MockFeatureService:
    def __init__(self) -> None:
        self.args = None

    async def get_value(
        self,
        feature: str,
        instrument: Optional[Instrument] = None,
    ) -> FeatureValue:
        self.args = (feature, instrument)
        return FeatureValue(
            feature=feature,
            instrument=instrument,
            value=Decimal("70.5"),
            ts_event=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
        )


def test_tool_specs_advertising() -> None:
    account = MockAccountService()
    # Case 1: only account service is present
    layer1 = ToolLayer(account)
    specs1 = layer1.tool_specs()
    names1 = [t["name"] for t in specs1]
    assert "get_balance" in names1
    assert "get_positions" in names1
    assert "get_orders" in names1
    assert "place_order" in names1
    assert "cancel_order" in names1
    assert "get_stock_quote" not in names1
    assert "get_option_quote" not in names1
    assert "get_crypto_quote" not in names1
    assert "get_stock_bars" not in names1
    assert "get_option_bars" not in names1
    assert "get_crypto_bars" not in names1
    assert "query_news" not in names1
    assert "get_factor" not in names1
    assert "get_rsi" not in names1
    assert "get_macd" not in names1

    # Case 2: all services present
    layer2 = ToolLayer(
        account,
        MockMarketService(),
        MockNewsService(),
        MockFeatureService(),
    )
    specs2 = layer2.tool_specs()
    names2 = [t["name"] for t in specs2]
    assert "get_balance" in names2
    assert "get_stock_quote" in names2
    assert "get_option_quote" in names2
    assert "get_crypto_quote" in names2
    assert "get_stock_bars" in names2
    assert "get_option_bars" in names2
    assert "get_crypto_bars" in names2
    assert "query_news" in names2
    assert "get_factor" in names2
    assert "get_rsi" in names2
    assert "get_macd" in names2

    rsi_spec = next(t for t in specs2 if t["name"] == "get_rsi")
    assert rsi_spec["parameters"]["required"] == ["symbol"]
    macd_spec = next(t for t in specs2 if t["name"] == "get_macd")
    assert macd_spec["parameters"]["required"] == ["symbol"]

    orders_spec = next(t for t in specs1 if t["name"] == "get_orders")
    assert orders_spec["parameters"]["properties"]["status"]["default"] == "open"
    assert orders_spec["parameters"]["properties"]["status"]["enum"] == [
        "open",
        "closed",
        "all",
    ]
    assert "symbol" in orders_spec["parameters"]["properties"]
    assert orders_spec["parameters"]["required"] == []


@pytest.mark.asyncio
async def test_dispatch_account_tools() -> None:
    account = MockAccountService()
    layer = ToolLayer(account)

    # get_balance
    balance_res = await layer.dispatch("get_balance", {})
    assert balance_res["cash"] == "10000"
    assert balance_res["buying_power"] == "20000"

    # get_positions
    positions_res = await layer.dispatch("get_positions", {})
    assert len(positions_res["positions"]) == 1
    assert positions_res["positions"][0]["instrument"]["symbol"] == "AAPL"

    # place_order
    order_args = {
        "client_order_id": "client-1",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": "5",
        "order_type": "limit",
        "limit_price": "175.50",
    }
    place_res = await layer.dispatch("place_order", order_args)
    assert place_res["broker_order_id"] == "broker-456"
    assert place_res["status"] == "pending_new"
    assert account.placed_order is not None
    assert account.placed_order.quantity == Decimal("5")
    assert account.placed_order.limit_price == Decimal("175.50")

    # crypto place_order: agent calls often omit tif, but Alpaca crypto market
    # orders cannot use the equity-oriented DAY default.
    crypto_args = {
        "client_order_id": "btc-buy-001-20260609",
        "symbol": "BTC/USD",
        "asset_class": "crypto",
        "side": "buy",
        "quantity": "0.001",
        "order_type": "market",
    }
    await layer.dispatch("place_order", crypto_args)
    assert account.placed_order is not None
    assert account.placed_order.instrument.asset_class == AssetClass.CRYPTO
    assert account.placed_order.tif == TimeInForce.GTC

    # cancel_order
    cancel_res = await layer.dispatch("cancel_order", {"broker_order_id": "broker-456"})
    assert cancel_res["status"] == "success"
    assert account.cancelled_broker_order_id == "broker-456"

    # get_orders: default to OPEN, no symbol filter
    open_res = await layer.dispatch("get_orders", {})
    assert len(open_res["orders"]) == 1
    assert open_res["orders"][0]["broker_order_id"] == "broker-789"
    assert account.get_orders_status == OrderFilter.OPEN
    assert account.get_orders_symbols is None

    # get_orders: status=all + symbol filter
    filtered_res = await layer.dispatch(
        "get_orders", {"status": "all", "symbol": "TSLA"}
    )
    assert filtered_res["orders"][0]["instrument"]["symbol"] == "TSLA"
    assert account.get_orders_status == OrderFilter.ALL
    assert account.get_orders_symbols == ["TSLA"]


@pytest.mark.asyncio
async def test_dispatch_market_tools() -> None:
    account = MockAccountService()
    market = MockMarketService()
    layer = ToolLayer(account, market=market)

    # get_stock_quote
    quote_res = await layer.dispatch("get_stock_quote", {"symbol": "AAPL"})
    assert quote_res["bid"] == "180.50"
    assert quote_res["ask"] == "180.60"
    assert market.instrument_quote.symbol == "AAPL"
    assert market.instrument_quote.asset_class == AssetClass.EQUITY

    # get_option_quote
    option_quote_res = await layer.dispatch(
        "get_option_quote", {"symbol": "AAPL260619C00150000"}
    )
    assert option_quote_res["bid"] == "180.50"
    assert option_quote_res["ask"] == "180.60"
    assert market.instrument_quote.symbol == "AAPL260619C00150000"
    assert market.instrument_quote.asset_class == AssetClass.OPTION

    # get_stock_bars
    stock_bars_res = await layer.dispatch(
        "get_stock_bars",
        {
            "symbol": "AAPL",
            "timeframe": "1m",
            "start": "2026-06-02T01:00:00Z",
            "end": "2026-06-02T02:00:00Z",
        },
    )
    assert len(stock_bars_res["bars"]) == 1
    assert stock_bars_res["bars"][0]["close"] == "180.50"
    assert market.bars_args[0].asset_class == AssetClass.EQUITY
    assert market.bars_args[1] == Timeframe.M1

    # get_option_bars
    option_bars_res = await layer.dispatch(
        "get_option_bars",
        {
            "symbol": "AAPL260619C00150000",
            "timeframe": "1m",
            "start": "2026-06-02T01:00:00Z",
            "end": "2026-06-02T02:00:00Z",
        },
    )
    assert len(option_bars_res["bars"]) == 1
    assert option_bars_res["bars"][0]["close"] == "180.50"
    assert market.bars_args[0].asset_class == AssetClass.OPTION
    assert market.bars_args[1] == Timeframe.M1

    # get_crypto_quote
    crypto_quote_res = await layer.dispatch("get_crypto_quote", {"symbol": "BTC/USD"})
    assert crypto_quote_res["bid"] == "180.50"
    assert crypto_quote_res["ask"] == "180.60"
    assert market.instrument_quote.symbol == "BTC/USD"
    assert market.instrument_quote.asset_class == AssetClass.CRYPTO

    # get_crypto_bars
    crypto_bars_res = await layer.dispatch(
        "get_crypto_bars",
        {
            "symbol": "BTC/USD",
            "timeframe": "1m",
            "start": "2026-06-02T01:00:00Z",
            "end": "2026-06-02T02:00:00Z",
        },
    )
    assert len(crypto_bars_res["bars"]) == 1
    assert crypto_bars_res["bars"][0]["close"] == "180.50"
    assert market.bars_args[0].asset_class == AssetClass.CRYPTO
    assert market.bars_args[0].symbol == "BTC/USD"
    assert market.bars_args[1] == Timeframe.M1


@pytest.mark.asyncio
async def test_dispatch_news_and_features_tools() -> None:
    account = MockAccountService()
    news = MockNewsService()
    features = MockFeatureService()
    layer = ToolLayer(account, news=news, features=features)

    # query_news
    news_res = await layer.dispatch(
        "query_news",
        {
            "symbols": ["AAPL"],
            "sources": ["mock_news"],
            "keywords": ["rising"],
            "since": "2026-06-02T00:00:00Z",
        },
    )
    assert len(news_res["news"]) == 1
    assert news_res["news"][0]["headline"] == "AAPL stock rises"
    assert news.filter.sources == ("mock_news",)

    # get_factor
    factor_res = await layer.dispatch(
        "get_factor", {"feature": "rsi_14", "symbol": "AAPL"}
    )
    assert factor_res["value"] == 70.5
    assert features.args[0] == "rsi_14"
    assert features.args[1].symbol == "AAPL"

    # get_rsi
    rsi_res = await layer.dispatch("get_rsi", {"symbol": "AAPL"})
    assert rsi_res["value"] == 70.5
    assert rsi_res["feature"] == "rsi"
    assert features.args[0] == "rsi"
    assert features.args[1].symbol == "AAPL"
    assert features.args[1].asset_class == AssetClass.EQUITY

    # get_macd
    macd_res = await layer.dispatch("get_macd", {"symbol": "AAPL"})
    assert macd_res["value"] == 70.5
    assert macd_res["feature"] == "macd"
    assert features.args[0] == "macd"
    assert features.args[1].symbol == "AAPL"
    assert features.args[1].asset_class == AssetClass.EQUITY


def test_stream_specs() -> None:
    account = MockAccountService()
    layer = ToolLayer(
        account, MockMarketService(), MockNewsService(), MockFeatureService()
    )
    streams = layer.stream_specs()
    names = [s["name"] for s in streams]
    assert "account_events" in names
    assert "market_events" in names
    assert "news_events" in names
    assert "feature_events" in names
