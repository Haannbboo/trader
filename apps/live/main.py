import sys
from pathlib import Path

# Path injection for local packages and namespace packages
root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "packages"))
for p in (root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(p))



import anyio
import os
from loguru import logger


from observability import setup_logging
from config import load_config
from bus import RedisStreamBus, InProcessBus
from plugins import registry
from market import MarketService
from news import NewsService
from account import AccountService
from feature import FeatureService
from feature.runtime import FeatureRuntime
from guardrail import Guardrail
from tools import ToolLayer
from agent import TraderAgentHarness

# Import all adapters and features to auto-register
import adapters.market.polygon
import adapters.market.alpaca
import adapters.news.benzinga
import adapters.news.rss
import adapters.account.alpaca
import features.technical.rsi
import features.technical.rolling_vol
import features.technical.returns
import features.crosssectional.rank
import features.sentiment


async def main() -> None:
    # 1. Setup telemetry
    setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))
    logger.info("Starting Trader Live System...")

    # 2. Load system configurations
    config = load_config("config/live.yaml")

    # 3. Initialize message bus
    # In live trading we use Redis Streams, or fallback to InProcess if redis is unavailable
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_url = f"redis://{redis_host}:{redis_port}"
    try:
        bus = RedisStreamBus(redis_url=redis_url)
        await bus.start()
    except Exception as e:
        logger.warning(f"Failed to start Redis bus: {e}. Falling back to InProcessBus.")
        bus = InProcessBus()
        await bus.start()

    # 4. Resolve and build enabled market & news adapters from configuration
    market_adapters = {}
    news_adapters = {}
    account_adapters = {}

    adapters_cfg = config.get("adapters", {})

    # Instantiate Market Adapters
    for item in adapters_cfg.get("market", []):
        name = item["name"]
        try:
            adapter_cls = registry.get("market", name)
            market_adapters[name] = adapter_cls()
            logger.info(f"Loaded market adapter '{name}' from configuration.")
        except Exception as e:
            logger.error(f"Failed to initialize market adapter '{name}': {e}")

    # Instantiate News Adapters
    for item in adapters_cfg.get("news", []):
        name = item["name"]
        try:
            adapter_cls = registry.get("news", name)
            news_adapters[name] = adapter_cls()
            logger.info(f"Loaded news adapter '{name}' from configuration.")
        except Exception as e:
            logger.error(f"Failed to initialize news adapter '{name}': {e}")

    # Instantiate Account/Broker portals
    for item in adapters_cfg.get("account", []):
        name = item["name"]
        try:
            adapter_cls = registry.get("account", name)
            account_adapters[name] = adapter_cls()
            logger.info(f"Loaded account adapter '{name}' from configuration.")
        except Exception as e:
            logger.error(f"Failed to initialize account adapter '{name}': {e}")

    # 5. Initialize guardrails and services
    guardrail = Guardrail(rules=[])

    market_service = MarketService(sources=list(market_adapters.values()), bus=bus)
    news_service = NewsService(sources=list(news_adapters.values()), bus=bus)
    account_service = AccountService(sources=list(account_adapters.values()), bus=bus, guardrail=guardrail)

    # Initialize feature runtime and service facade
    feature_runtime = FeatureRuntime(bus=bus)
    feature_service = FeatureService(runtime=feature_runtime)

    # 6. Instantiate features dynamically
    features_cfg = config.get("features", {})
    for category, list_items in features_cfg.items():
        for item in list_items:
            feat_name = item["name"]
            try:
                proc_cls = registry.get("feature", feat_name)
                proc = proc_cls()
                proc.initialize(item)
                feature_runtime.add_processor(proc)
                logger.info(f"Loaded and initialized feature '{feat_name}' ({category}).")
            except Exception as e:
                logger.error(f"Failed to load feature '{feat_name}': {e}")

    # 7. Setup agent tools
    tools = ToolLayer(
        market=market_service,
        news=news_service,
        account=account_service,
        features=feature_service
    )

    agent = TraderAgentHarness(
        bus=bus,
        tools=tools,
        guardrail=guardrail,
        strategy_config=config.get("agent", {})
    )

    # 8. Start system runtimes
    async with anyio.create_task_group() as tg:
        logger.info("Booting live runtime engines...")
        await market_service.start()
        await news_service.start()
        await account_service.start()
        await feature_runtime.start()
        await agent.start()

        # Subscribe tickers for all configured symbols
        # Under MarketDataService, subscribe is called directly
        # e.g., market_service.subscribe(instruments, channels)
        pass


        logger.info("Trader Live System is fully operational. Running event loop...")

        # Maintain runtime execution
        try:
            while True:
                await anyio.sleep(1.0)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Shutdown signal received.")
        finally:
            logger.info("Stopping runtimes...")
            await agent.stop()
            await market_service.stop()
            await news_service.stop()
            await account_service.stop()
            await bus.stop()


if __name__ == "__main__":
    anyio.run(main)
