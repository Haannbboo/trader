"""
tools — ToolLayer v1. Wraps domain SERVICES into Pi Agent tools.

The dimension that matters here is DOMAIN/CAPABILITY, not source count:
  - "how many market sources exist" is invisible here — MarketDataService already
    aggregated them behind get_stock_quote/get_option_quote. Adding the 50th polygon-vs-ibkr source
    changes nothing in this file.
  - what IS modeled here: the kinds of capabilities (quote, bars, news, balance,
    order, factor) — one tool per capability, each routed to the owning service.

So tool COUNT scales with capability variety, never with source variety.

v1: account is real (you have Alpaca). The other services are accepted as
optional so you can wire them in as they come online; tools for a missing
service simply aren't advertised. Depends only on the service Protocols in
ta.contracts.ports — never on a concrete adapter or service impl.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from contracts import (
    AccountService,
    AssetClass,
    FeatureService,
    Instrument,
    MarketDataService,
    NewsFilter,
    NewsService,
    Order,
    OrderType,
    Side,
    Timeframe,
    TimeInForce,
)

from tools.mcp import McpClient


class ToolLayer:
    def __init__(
        self,
        account: AccountService,
        market: Optional[MarketDataService] = None,
        news: Optional[NewsService] = None,
        features: Optional[FeatureService] = None,
        mcp_configs: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """Holds one reference PER DOMAIN (not per source). Optional ones may be
        None in v1; tool_specs() only advertises tools whose service is present."""
        self._account = account
        self._market = market
        self._news = news
        self._features = features
        self._mcp_clients: list[McpClient] = []
        if mcp_configs:
            for cfg in mcp_configs:
                if cfg.get("enabled", True):
                    self._mcp_clients.append(
                        McpClient(
                            name=cfg["name"],
                            url=cfg["url"],
                            tools_filter=cfg.get("tools"),
                        )
                    )

    async def initialize(self) -> None:
        """Asynchronously initialize all enabled MCP clients and fetch their tool definitions."""
        for client in self._mcp_clients:
            await client.list_tools()

        # Check for duplicate tool names across MCP clients and native tools
        seen_tools: dict[str, str] = (
            {}
        )  # tool_name -> source ("native" or "mcp:{client_name}")
        native_names = {
            "get_balance",
            "get_positions",
            "place_order",
            "cancel_order",
            "get_stock_quote",
            "get_option_quote",
            "get_stock_bars",
            "get_option_bars",
            "query_news",
            "get_factor",
        }
        for name in native_names:
            seen_tools[name] = "native"

        for client in self._mcp_clients:
            for spec in client.cached_specs:
                tool_name = spec.get("name")
                if not tool_name:
                    continue
                if tool_name in seen_tools:
                    raise ValueError(
                        f"Duplicate tool name '{tool_name}' detected. "
                        f"Exposed by both '{seen_tools[tool_name]}' and MCP client '{client.name}'."
                    )
                seen_tools[tool_name] = f"mcp:{client.name}"

    async def close(self) -> None:
        """Asynchronously close all MCP clients."""
        for client in self._mcp_clients:
            await client.close()

    def tool_specs(self) -> list[dict]:
        """Pi Agent tool schemas. One spec per capability of each PRESENT service.
        Consider: param JSON-schema, descriptions, and which tools to gate behind
        permissions (place_order is the dangerous one)."""
        specs = []

        # Account tools (always present)
        specs.append(
            {
                "name": "get_balance",
                "description": "Fetch cash, equity, and buying power balances for the account.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            }
        )
        specs.append(
            {
                "name": "get_positions",
                "description": "Fetch all currently open positions in the account.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            }
        )
        specs.append(
            {
                "name": "place_order",
                "description": "Place a new order (buy/sell). Enforced through risk guardrails.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "client_order_id": {
                            "type": "string",
                            "description": "Unique client order ID for idempotency.",
                        },
                        "symbol": {
                            "type": "string",
                            "description": "Symbol of the instrument (e.g. AAPL).",
                        },
                        "asset_class": {
                            "type": "string",
                            "enum": ["equity", "option", "crypto"],
                            "default": "equity",
                            "description": "Asset class of the instrument.",
                        },
                        "side": {
                            "type": "string",
                            "enum": ["buy", "sell"],
                            "description": "Order side (buy or sell).",
                        },
                        "quantity": {
                            "type": "string",
                            "description": "Quantity to trade.",
                        },
                        "order_type": {
                            "type": "string",
                            "enum": ["market", "limit", "stop", "stop_limit"],
                            "default": "market",
                            "description": "Type of the order.",
                        },
                        "limit_price": {
                            "type": "string",
                            "description": "Limit price for limit/stop-limit orders.",
                        },
                        "tif": {
                            "type": "string",
                            "enum": ["day", "gtc", "ioc", "fok"],
                            "default": "day",
                            "description": "Time in force.",
                        },
                    },
                    "required": ["client_order_id", "symbol", "side", "quantity"],
                },
            }
        )
        specs.append(
            {
                "name": "cancel_order",
                "description": "Cancel a pending order by its broker order ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "broker_order_id": {
                            "type": "string",
                            "description": "The unique broker order ID to cancel.",
                        }
                    },
                    "required": ["broker_order_id"],
                },
            }
        )

        if self._market is not None:
            specs.append(
                {
                    "name": "get_stock_quote",
                    "description": "Fetch the current bid/ask quote for a stock instrument.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Symbol of the stock (e.g. AAPL).",
                            },
                        },
                        "required": ["symbol"],
                    },
                }
            )
            specs.append(
                {
                    "name": "get_option_quote",
                    "description": "Fetch the current bid/ask quote for an option instrument.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "OCC Symbol of the option (e.g. AAPL260619C00150000).",
                            },
                        },
                        "required": ["symbol"],
                    },
                }
            )
            specs.append(
                {
                    "name": "get_stock_bars",
                    "description": "Fetch historical bars for a stock instrument.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Symbol of the stock (e.g. AAPL).",
                            },
                            "timeframe": {
                                "type": "string",
                                "enum": ["1s", "1m", "5m", "15m", "1h", "1d"],
                                "description": "Bar timeframe size.",
                            },
                            "start": {
                                "type": "string",
                                "description": "Start ISO-8601 timestamp (e.g. 2026-06-01T00:00:00Z) in UTC.",
                            },
                            "end": {
                                "type": "string",
                                "description": "End ISO-8601 timestamp (e.g. 2026-06-02T00:00:00Z) in UTC.",
                            },
                        },
                        "required": ["symbol", "timeframe", "start", "end"],
                    },
                }
            )
            specs.append(
                {
                    "name": "get_option_bars",
                    "description": "Fetch historical bars for an option instrument.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "OCC Symbol of the option (e.g. AAPL260619C00150000).",
                            },
                            "timeframe": {
                                "type": "string",
                                "enum": ["1s", "1m", "5m", "15m", "1h", "1d"],
                                "description": "Bar timeframe size.",
                            },
                            "start": {
                                "type": "string",
                                "description": "Start ISO-8601 timestamp (e.g. 2026-06-01T00:00:00Z) in UTC.",
                            },
                            "end": {
                                "type": "string",
                                "description": "End ISO-8601 timestamp (e.g. 2026-06-02T00:00:00Z) in UTC.",
                            },
                        },
                        "required": ["symbol", "timeframe", "start", "end"],
                    },
                }
            )

        if self._news is not None:
            specs.append(
                {
                    "name": "query_news",
                    "description": "Query news items matching filters.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbols": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of symbols to filter news by.",
                            },
                            "sources": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of news sources to filter by.",
                            },
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of keywords to filter by.",
                            },
                            "since": {
                                "type": "string",
                                "description": "Only query news published since this ISO timestamp.",
                            },
                        },
                        "required": [],
                    },
                }
            )

        if self._features is not None:
            specs.append(
                {
                    "name": "get_factor",
                    "description": "Get a derived factor or technical signal value (e.g. rsi_14).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "feature": {
                                "type": "string",
                                "description": "The name of the feature/factor (e.g. rsi_14, rolling_vol_20).",
                            },
                            "symbol": {
                                "type": "string",
                                "description": "Optional symbol to evaluate the feature for.",
                            },
                            "asset_class": {
                                "type": "string",
                                "enum": ["equity", "option", "crypto"],
                                "default": "equity",
                                "description": "Optional asset class of the instrument.",
                            },
                        },
                        "required": ["feature"],
                    },
                }
            )

        for client in self._mcp_clients:
            for spec in client.cached_specs:
                mapped_spec = dict(spec)
                schema = mapped_spec.get("inputSchema")
                if not schema:
                    schema = {"type": "object", "properties": {}}
                mapped_spec["parameters"] = schema
                specs.append(mapped_spec)

        return specs

    async def dispatch(self, name: str, args: dict) -> dict:
        """Route ONE tool call to the owning service method; validate args;
        serialize the result. Maps:
            get_balance/get_positions/place_order/cancel_order -> account
            get_stock_quote/get_option_quote/get_stock_bars/get_option_bars -> market
            query_news                                         -> news
            get_factor                                         -> features
        place_order goes through AccountService (-> guardrail); the tool layer
        must not bypass it with a direct source call."""
        if name == "get_balance":
            balance = await self._account.get_balance()
            return self._serialize(balance)
        elif name == "get_positions":
            positions = await self._account.get_positions()
            return {"positions": self._serialize(positions)}
        elif name == "place_order":
            order = self._order_from_args(args)
            res = await self._account.place_order(order)
            return self._serialize(res)
        elif name == "cancel_order":
            await self._account.cancel_order(args["broker_order_id"])
            return {"status": "success"}
        elif name == "get_stock_quote":
            if self._market is None:
                raise ValueError("Market service is not available")
            instrument = Instrument(
                symbol=args["symbol"], asset_class=AssetClass.EQUITY
            )
            quote = await self._market.get_quote(instrument)
            return self._serialize(quote)
        elif name == "get_option_quote":
            if self._market is None:
                raise ValueError("Market service is not available")
            instrument = Instrument(
                symbol=args["symbol"], asset_class=AssetClass.OPTION
            )
            quote = await self._market.get_quote(instrument)
            return self._serialize(quote)
        elif name == "get_stock_bars":
            if self._market is None:
                raise ValueError("Market service is not available")
            instrument = Instrument(
                symbol=args["symbol"], asset_class=AssetClass.EQUITY
            )
            timeframe = Timeframe(args["timeframe"])
            start = datetime.fromisoformat(args["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(args["end"].replace("Z", "+00:00"))
            bars = await self._market.get_bars(instrument, timeframe, start, end)
            return {"bars": self._serialize(bars)}
        elif name == "get_option_bars":
            if self._market is None:
                raise ValueError("Market service is not available")
            instrument = Instrument(
                symbol=args["symbol"], asset_class=AssetClass.OPTION
            )
            timeframe = Timeframe(args["timeframe"])
            start = datetime.fromisoformat(args["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(args["end"].replace("Z", "+00:00"))
            bars = await self._market.get_bars(instrument, timeframe, start, end)
            return {"bars": self._serialize(bars)}
        elif name == "query_news":
            if self._news is None:
                raise ValueError("News service is not available")
            instruments = tuple(
                Instrument(symbol=sym, asset_class=AssetClass.EQUITY)
                for sym in args.get("symbols", [])
            )
            since = None
            if args.get("since"):
                since = datetime.fromisoformat(args["since"].replace("Z", "+00:00"))
            flt = NewsFilter(
                instruments=instruments,
                sources=tuple(args.get("sources", [])),
                keywords=tuple(args.get("keywords", [])),
                since=since,
            )
            news_items = await self._news.query(flt)
            return {"news": self._serialize(news_items)}
        elif name == "get_factor":
            if self._features is None:
                raise ValueError("Feature service is not available")
            instrument = None
            if args.get("symbol"):
                instrument = Instrument(
                    symbol=args["symbol"],
                    asset_class=AssetClass(args.get("asset_class", "equity")),
                )
            val = await self._features.get_value(args["feature"], instrument)
            return self._serialize(val)
        else:
            for client in self._mcp_clients:
                if any(spec["name"] == name for spec in client.cached_specs):
                    res = await client.call_tool(name, args)
                    return self._serialize(res)
            raise ValueError(f"Unknown tool name: {name}")

    def stream_specs(self) -> list[dict]:
        """Subscriptions exposed to the agent loop (fills, quotes, news, factors)
        — how a streaming source becomes something the agent can consume. Each
        maps to the corresponding service.subscribe()."""
        specs = []

        specs.append(
            {
                "name": "account_events",
                "description": "Stream of fills, order updates, balance updates, and position updates.",
                "service": "account",
            }
        )

        if self._market is not None:
            specs.append(
                {
                    "name": "market_events",
                    "description": "Stream of quotes and bar events.",
                    "service": "market",
                }
            )

        if self._news is not None:
            specs.append(
                {
                    "name": "news_events",
                    "description": "Stream of news item events.",
                    "service": "news",
                }
            )

        if self._features is not None:
            specs.append(
                {
                    "name": "feature_events",
                    "description": "Stream of computed features and signals.",
                    "service": "features",
                }
            )

        return specs

    @staticmethod
    def _serialize(data: Any) -> Any:
        if hasattr(data, "model_dump"):
            return data.model_dump(mode="json")
        if isinstance(data, list):
            return [ToolLayer._serialize(x) for x in data]
        if isinstance(data, dict):
            return {k: ToolLayer._serialize(v) for k, v in data.items()}
        if isinstance(data, Decimal):
            return str(data)
        return data

    # --- small, fully-written helper: building an Order from tool args ---
    @staticmethod
    def _order_from_args(args: dict) -> Order:
        """Translate flat agent args into a typed Order. Kept explicit because
        this is the boundary where loose tool input becomes a money-moving DTO —
        validate hard here."""
        return Order(
            client_order_id=args["client_order_id"],
            instrument=Instrument(
                symbol=args["symbol"],
                asset_class=AssetClass(args.get("asset_class", "equity")),
            ),
            side=Side(args["side"]),
            quantity=Decimal(str(args["quantity"])),
            order_type=OrderType(args.get("order_type", "market")),
            limit_price=(
                Decimal(str(args["limit_price"]))
                if args.get("limit_price") is not None
                else None
            ),
            tif=TimeInForce(args.get("tif", "day")),
        )
