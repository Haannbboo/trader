# ruff: noqa: E402
import sys
from pathlib import Path

# Path injection for local packages and namespace packages
root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "packages"))
for p in (root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(p))


import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import anyio
from account import AccountService
from agent import TraderAgentHarness
from bus import InProcessBus
from contracts import (
    AssetClass,
    Bar,
    EventType,
    Instrument,
    Subscription,
    Timeframe,
)
from feature import FeatureService
from feature.runtime import FeatureRuntime
from guardrail import Guardrail
from loguru import logger
from observability import setup_logging
from persistence import Database, DbWriter, Repository
from plugins import registry
from tools import ToolLayer


async def main() -> None:
    # 1. Setup logs & discover plugins
    setup_logging(level="INFO")
    logger.info("=== Starting Backtest Engine ===")
    from plugins import discover

    discover(
        [
            "adapters.account.alpaca",
            "features.technical.rsi",
            "features.technical.returns",
        ]
    )

    import yaml

    with open("config/backtest.yaml", "r") as f:
        config = yaml.safe_load(f) or {}
    bt_cfg = config.get("backtest", {})
    start_date = bt_cfg.get("start_date", "2026-01-01")
    end_date = bt_cfg.get("end_date", "2026-05-31")

    logger.info(f"Replaying history from {start_date} to {end_date}")

    # 3. Initialize persistence database & HistoryStore repository
    persistence_cfg = config.get("infra", {}).get("persistence", {})
    dsn = persistence_cfg.get("dsn", "sqlite+aiosqlite:///./backtest.db")
    logger.info(f"Connecting to backtest database at: {dsn}")
    db = Database(dsn, echo=False)
    await db.create_all()
    repository = Repository(db)

    # Initialize bus & feature processors
    bus = InProcessBus()
    await bus.start()

    feature_runtime = FeatureRuntime(bus=bus)
    feature_service = FeatureService(runtime=feature_runtime)

    # Instantiate and register RSI
    rsi = registry.get("feature", "rsi")(period=14)
    rsi.initialize()
    feature_runtime.add_processor(rsi)

    # Instantiate and register returns (if implemented)
    try:
        returns = registry.get("feature", "returns")()
        returns.initialize({"lag": 1})
        feature_runtime.add_processor(returns)
    except NotImplementedError:
        logger.warning(
            "Feature 'returns' is unimplemented and will be skipped in this backtest."
        )

    mock_account = registry.get("account", "alpaca")(api_key="fake", api_secret="fake")
    await mock_account.start()

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

    # 5. Populate database if empty
    symbols = ["AAPL", "MSFT"]
    async with db.session() as session:
        from persistence.models import BarRow
        from sqlalchemy import func, select

        count = (
            await session.execute(select(func.count(BarRow.instrument_key)))
        ).scalar() or 0

    if count == 0:
        logger.info(
            "Database is empty. Pre-populating database with mock historical bars..."
        )
        writer = DbWriter(db)
        current_time = datetime.strptime(start_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        end_time = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        price_state = {"AAPL": 150.0, "MSFT": 350.0}

        bars_to_insert = []
        step = 0
        while current_time <= end_time:
            for symbol in symbols:
                if step == 10 and symbol == "AAPL":
                    price_state[symbol] = 110.0
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
                bars_to_insert.append(bar)
            current_time += timedelta(days=1)
            step += 1

        await writer.store_bars(bars_to_insert, source="backtest")
        logger.info(f"Successfully populated database with {len(bars_to_insert)} bars.")

    # 6. Start runtime, agent, and logging listener
    async def log_features():
        logger.info("Starting background logger for emitted FEATURE events...")
        try:
            async for ev in bus.subscribe(
                Subscription(event_types=(EventType.FEATURE,))
            ):
                logger.info(
                    f"[FEATURE DETECTED] {ev.payload.feature} "
                    f"value={ev.payload.value:.4f} signal={ev.payload.meta.get('signal')} "
                    f"for {ev.payload.instrument.symbol} as of {ev.payload.ts_event}"
                )
        except asyncio.CancelledError:
            pass

    feature_logging_task = asyncio.create_task(log_features())

    await feature_runtime.start()
    try:
        await agent.start()
    except NotImplementedError:
        logger.warning(
            "Agent harness is unimplemented and will be skipped in this backtest."
        )
        agent = None

    # Allow a brief moment for subscriptions to register
    await asyncio.sleep(0.05)

    # 7. Replay loop using bus.replay
    logger.info("Starting historical event replay from database via bus.replay()...")
    start_time = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_time = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    replay_count = 0
    async for event in bus.replay(
        subscription=Subscription(
            event_types=(EventType.BAR,),
            instruments=tuple(
                Instrument(symbol=sym, asset_class=AssetClass.EQUITY) for sym in symbols
            ),
        ),
        start=start_time,
        end=end_time + timedelta(days=1),
        history=repository,
    ):
        await bus.publish(event)
        replay_count += 1

    logger.info(f"Replayed {replay_count} events from database.")

    # Allow async queue to process any outstanding signals
    await anyio.sleep(1.0)
    feature_logging_task.cancel()

    # 8. Retrieve Backtest Results
    try:
        positions = await account_service.get_positions()
        balances = await account_service.get_balance()
        logger.info("=== Backtest Complete ===")
        logger.info(f"Final Account Balance: {balances}")
        logger.info(f"Open Positions: {positions}")
    except Exception as e:
        logger.warning(
            f"Could not retrieve live account state (unauthorized/offline): {e}"
        )
        logger.info("=== Backtest Complete ===")

    # Shutdown
    if agent is not None:
        try:
            await agent.stop()
        except NotImplementedError:
            pass
    await bus.stop()


if __name__ == "__main__":
    anyio.run(main)
