import sys
from pathlib import Path

# Path injection for local packages and namespace packages
root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "packages"))
for p in (root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(p))


import anyio
from loguru import logger


from observability import setup_logging
from bus import InProcessBus
from plugins import registry
from market import MarketService
from news import NewsService
from account import AccountService
from feature import FeatureService
from feature.runtime import FeatureRuntime
from guardrail import Guardrail
from tools import ToolLayer
from agent import TraderAgentHarness

# Make sure all adapters and features are imported so they register themselves!


async def main() -> None:
    # 1. Setup logs
    setup_logging(level="INFO")
    logger.info("=== Starting Trader Smoke Test (Vertical Slice) ===")

    # 2. Setup Bus
    bus = InProcessBus()
    await bus.start()

    # 3. Instantiate dynamic adapters from plugin registry
    logger.info("Instantiating adapters from registry...")
    polygon_market = registry.get("market", "polygon")()
    benzinga_news = registry.get("news", "benzinga")()
    alpaca_account = registry.get("account", "alpaca")()

    # 4. Initialize guardrails and services
    guardrail = Guardrail(rules=[])

    market_service = MarketService(sources=[polygon_market], bus=bus)
    news_service = NewsService(sources=[benzinga_news], bus=bus)
    account_service = AccountService(
        sources=[alpaca_account], bus=bus, guardrail=guardrail
    )

    # Initialize feature runtime and service facade
    feature_runtime = FeatureRuntime(bus=bus)
    feature_service = FeatureService(runtime=feature_runtime)

    rsi_processor = registry.get("feature", "rsi")()
    sentiment_processor = registry.get("feature", "sentiment")()

    # Initialize processors with configs
    rsi_processor.initialize({"period": 14})
    sentiment_processor.initialize({"model": "bert-sentiment-mock"})

    # feature_runtime.add_processor(rsi_processor)
    # feature_runtime.add_processor(sentiment_processor)

    # 5. Initialize agent loop
    tools = ToolLayer(
        market=market_service,
        news=news_service,
        account=account_service,
        features=feature_service,
    )
    agent = TraderAgentHarness(
        bus=bus,
        tools=tools,
        guardrail=guardrail,
        strategy_config={"type": "trend_following"},
    )

    # 6. Start all runtimes
    await market_service.start()
    await news_service.start()
    await account_service.start()
    await feature_runtime.start()
    await agent.start()

    # 7. Subscribe agent tools to ticker streams
    logger.info("Wired agent tools to subscribe to AAPL...")
    # Under the new ToolLayer, subscriptions are defined via stream_specs()
    tools.stream_specs()

    # 8. Let the simulation run for a few seconds to process ticks and news
    logger.info("Running simulation for 8 seconds to process data stream...")
    await anyio.sleep(8.0)

    # 9. Stop all runtimes
    logger.info("Shutting down vertical slice...")
    await agent.stop()
    await market_service.stop()
    await news_service.stop()
    await account_service.stop()
    await bus.stop()
    logger.info("=== Smoke Test Finished Successfully ===")


if __name__ == "__main__":
    anyio.run(main)
