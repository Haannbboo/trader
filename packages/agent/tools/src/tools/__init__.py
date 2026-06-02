from __future__ import annotations

from contracts.ports import (
    AccountService,
    FeatureService,
    MarketDataService,
    NewsService,
)


class ToolLayer:
    """Wraps services + feature service into Pi Agent tool definitions.

    THIN: validate args, adapt async streams into agent-consumable form,
    enforce the boundary that ALL trading flows through AccountService (-> guardrail).
    No business logic lives here. Depends on the service interfaces only.
    """

    def __init__(
        self,
        market: MarketDataService,
        news: NewsService,
        account: AccountService,
        features: FeatureService,
    ) -> None:
        """Initialize the tool layer with the aggregated service interfaces."""
        self.market = market
        self.news = news
        self.account = account
        self.features = features

    def tool_specs(self) -> list[dict]:
        """Pi Agent tool schemas: name, description, JSON-schema params.

        One spec per capability (get_quote, get_bars, query_news, get_factor, place_order...).
        """
        raise NotImplementedError()

    async def dispatch(self, name: str, args: dict) -> dict:
        """Route a single tool call from the agent to the right service method;

        validate args; serialize the result back to the agent.
        """
        raise NotImplementedError()

    def stream_specs(self) -> list[dict]:
        """Subscriptions exposed to the agent (quotes/news/fills/factors) — how a

        streaming source becomes something the agent loop can consume.
        """
        raise NotImplementedError()
