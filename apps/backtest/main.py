# ruff: noqa: E402
import sys
from pathlib import Path

# Path injection for local packages and namespace packages
root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "packages"))
for p in (root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(p))


from datetime import datetime, timedelta, timezone
from decimal import Decimal

import anyio
from account import AccountService
from agent import TraderAgentHarness
from bus import InProcessBus
from contracts import AssetClass, Bar, Event, EventType, Instrument, Timeframe
from feature import FeatureService
from feature.runtime import FeatureRuntime
from guardrail import Guardrail
from loguru import logger
from observability import setup_logging
from plugins import registry
from tools import ToolLayer


async def main() -> None:
    # 1. Setup logs
    setup_logging(level="INFO")
    logger.info("=== Starting Backtest Engine ===")

    import yaml

    with open("config/backtest.yaml", "r") as f:
        config = yaml.safe_load(f) or {}
    bt_cfg = config.get("backtest", {})
    start_date = bt_cfg.get("start_date", "2026-01-01")
    end_date = bt_cfg.get("end_date", "2026-05-31")

    logger.info(f"Replaying history from {start_date} to {end_date}")

    # 3. Initialize bus & feature processors
    bus = InProcessBus()
    await bus.start()

    feature_runtime = FeatureRuntime(bus=bus)
    feature_service = FeatureService(runtime=feature_runtime)
    rsi = registry.get("feature", "rsi")()
    rsi.initialize({"period": 14})
    returns = registry.get("feature", "returns")()
    returns.initialize({"lag": 1})

    feature_runtime.add_processor(rsi)
    feature_runtime.add_processor(returns)

    # 4. Initialize account and tools (using mock adapters for backtest simulation)
    mock_account = registry.get("account", "alpaca")()
    await mock_account.connect()

    guardrail = Guardrail(rules=[])
    account_service = AccountService(
        sources=[mock_account], bus=bus, guardrail=guardrail
    )

    # Pre-populate some historical features
    tools = ToolLayer(
        market=None,
        news=None,
        account=account_service,
        features=feature_service,
    )

    agent = TraderAgentHarness(
        bus=bus,
        tools=tools,
        guardrail=guardrail,
        strategy_config=config.get("agent", {}),
    )

    await feature_runtime.start()
    await agent.start()

    # 5. Replay loop (generate a deterministic series of bars simulating historical data)
    symbols = ["AAPL", "MSFT"]
    current_time = datetime.strptime(start_date, "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    )
    end_time = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    price_state = {"AAPL": 150.0, "MSFT": 350.0}

    logger.info("Injecting deterministic historical bars...")
    # Fast-forward time, e.g. generate daily bars
    step = 0
    while current_time <= end_time:
        for symbol in symbols:
            # Simple deterministically varying price to simulate historical data
            # Creates an artificial oversold DIP at step 10 to trigger RSI Buy
            if step == 10 and symbol == "AAPL":
                price_state[symbol] = 110.0  # Big drop
            else:
                price_state[symbol] += (step % 5 - 2) * 1.5

            instrument = Instrument(symbol=symbol, asset_class=AssetClass.EQUITY)
            bar = Bar(
                instrument=instrument,
                timeframe=Timeframe.D1,
                ts_open=current_time,
                open=Decimal(str(price_state[symbol] - 1.0)),
                high=Decimal(str(price_state[symbol] + 2.0)),
                low=Decimal(str(price_state[symbol] - 2.0)),
                close=Decimal(str(price_state[symbol])),
                volume=Decimal(str(10000 + step * 100)),
            )
            event = Event(
                type=EventType.BAR, source="backtest", payload=bar, ts_event=bar.ts_open
            )
            # Publish to the bus which triggers FeatureService processing
            await bus.publish(event)

        current_time += timedelta(days=1)
        step += 1

    # Allow async queue to process any outstanding signals
    await anyio.sleep(1.0)

    # 6. Retrieve Backtest Results
    positions = await account_service.get_positions()
    balances = await account_service.get_balance()
    logger.info("=== Backtest Complete ===")
    logger.info(f"Final Account Balance: {balances}")
    logger.info(f"Open Positions: {positions}")

    # Shutdown
    await agent.stop()
    await bus.stop()


if __name__ == "__main__":
    anyio.run(main)
