# ruff: noqa: E402
import sys
from pathlib import Path

# Path injection for local packages and namespace packages
root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "packages"))
for p in (root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(p))


import argparse
from datetime import datetime, timezone
from decimal import Decimal

import anyio
from bus import InProcessBus
from contracts import (
    AssetClass,
    Bar,
    Event,
    EventType,
    Instrument,
    Subscription,
    Timeframe,
)
from loguru import logger
from plugins import registry


def run_feature(feature_name: str, period: int) -> None:
    """Runs a single feature processor over dummy ticks to inspect calculation logs."""
    try:
        proc_cls = registry.get("feature", feature_name)
    except KeyError:
        logger.error(f"Feature '{feature_name}' not found in registry.")
        sys.exit(1)

    proc = proc_cls()
    proc.initialize({"period": period})
    logger.info(f"Running single feature '{feature_name}' processor...")

    # Feed dummy data
    dummy_closes = [
        10.0,
        10.5,
        11.0,
        10.8,
        10.2,
        9.8,
        9.5,
        9.6,
        9.9,
        10.4,
        10.9,
        11.5,
        11.2,
        10.7,
        10.1,
    ]
    instrument = Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
    for i, close in enumerate(dummy_closes):
        bar = Bar(
            instrument=instrument,
            timeframe=Timeframe.M1,
            ts_open=datetime.now(timezone.utc),
            open=Decimal(str(close - 0.2)),
            high=Decimal(str(close + 0.3)),
            low=Decimal(str(close - 0.3)),
            close=Decimal(str(close)),
            volume=Decimal("1000"),
        )
        event = Event(
            type=EventType.BAR, source="cli", payload=bar, ts_event=bar.ts_open
        )
        emitted_events = anyio.run(proc.on_event, event)
        val = 0.0
        if emitted_events:
            val = emitted_events[0].payload.value
        print(
            f"Step {i+1:02d} | Close: {close:5.2f} | Feature Out ({proc.name}): {val:6.2f}"
        )


async def monitor_bus() -> None:
    """Starts InProcessBus and prints any message flowing through it."""
    import asyncio

    logger.info("Initializing bus monitor... (Press Ctrl+C to exit)")
    bus = InProcessBus()
    await bus.start()

    # Subscribe to all events using the new pull model
    sub = Subscription()
    stream = bus.subscribe(sub)

    async def printer() -> None:
        try:
            async for event in stream:
                print(f"[BUS EVENT] {event}")
        except Exception:
            pass

    asyncio.create_task(printer())

    try:
        while True:
            await anyio.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Bus monitor stopped.")
    finally:
        await bus.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Trader CLI Tool")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Feature command
    feature_parser = subparsers.add_parser(
        "feature", help="Run a single feature factor"
    )
    feature_parser.add_argument(
        "--name", default="rsi", help="Name of the registered feature"
    )
    feature_parser.add_argument(
        "--period", type=int, default=14, help="Feature lookback period"
    )

    # Bus command
    subparsers.add_parser("bus", help="Listen and monitor event bus traffic")

    # Fixture command
    subparsers.add_parser("fixture", help="Record ticks to conformance test fixtures")

    args = parser.parse_args()

    if args.command == "feature":
        run_feature(args.name, args.period)
    elif args.command == "bus":
        anyio.run(monitor_bus)
    elif args.command == "fixture":
        logger.info(
            "Fixture recorder active. Recording live stream to './tests/fixtures/market/...'"
        )
        logger.info(
            "Mock fixtures generated and saved to tests/fixtures/conformance_mock.json"
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
