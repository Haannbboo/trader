# ruff: noqa: E402
"""
apps/smoke/main.py — the thin vertical slice, runnable.

Proves the contract end-to-end: build a bus, inject ONE account adapter into the
minimal AccountService, start the event pump, then (a) call tools synchronously
like an agent would and (b) watch fills stream off the bus.

Run:
    python -m apps.smoke.main mock        # mock account adapter; market read follows config
    python -m apps.smoke.main alpaca      # real Alpaca paper account + configured market read

The account side is selected by mode. Everything downstream of the account
adapter is identical. The market read path is separately config-driven so it can
exercise the registered market adapter even while the account side is mocked.

The bus impl is also driven by config: if `infra.bus.url` is set in
config/smoke.yaml the slice uses RedisStreamBus (start `just up` first); if
not, it falls back to InProcessBus. That's the same swap-in-place story, one
layer up.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Path injection for local packages and namespace packages
root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "packages"))
for p in (root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(p))

import asyncio

from account import AccountService  # pyrefly: ignore [missing-import]
from bus import Bus, InProcessBus  # pyrefly: ignore [missing-import]
from contracts import (
    AccountSourcePort,
    AssetClass,
    Instrument,
    MarketSourcePort,
    Timeframe,
)
from guardrail import Guardrail  # pyrefly: ignore [missing-import]
from persistence import (
    Database,
    PersistenceWriter,
    Repository,
)  # pyrefly: ignore [missing-import]
from tools import ToolLayer  # pyrefly: ignore [missing-import]

from apps.smoke.mock_adapter import MockAccountAdapter
from config import AppConfig, SourceSettings  # pyrefly: ignore [missing-import]

# Smoke-local knobs: timeframe + lookback window for the market bars query.
# These describe WHAT the smoke asks of the adapter, not WHAT the adapter is.
_BAR_TIMEFRAME = Timeframe.M1
_BAR_LOOKBACK = timedelta(hours=1)


def build_adapter(mode: str, cfg: AppConfig) -> AccountSourcePort:
    """The one switch point. mock = offline; alpaca = real paper account."""
    if mode == "mock":
        return MockAccountAdapter(n_fills=3, interval_s=0.3)
    if mode == "alpaca":
        # Real path: config resolves the secret and splats it into the ctor,
        # exactly as registry.build_sources would in apps/live.
        from adapters.account.alpaca import AlpacaAccountAdapter  # your real adapter

        params = cfg.source_params("account", "alpaca")
        return AlpacaAccountAdapter(**params)
    raise SystemExit(f"unknown mode {mode!r}; use 'mock' or 'alpaca'")


def build_market_adapter(cfg: AppConfig, src: SourceSettings) -> MarketSourcePort:
    """Instantiate a registered market adapter from yaml + .env credentials.

    ``source`` and optional ``name`` match the registry key directly, so this
    builder no longer decomposes a flat yaml name. Source-specific yaml keys
    such as ``feed`` and ``instruments`` live in ``params``.
    """
    from plugins import discover, registry

    # Both stock + option live in the `adapters.market.alpaca` package; the
    # @register decorator runs on import. Discovering the package once is
    # enough regardless of which adapter name is being looked up.
    discover(["adapters.market.alpaca"])

    params = cfg.source_params("market", src.source, src.name)
    cls = registry.get("market", src.source, src.name)
    return cls(**params)  # type: ignore[abstract]


def parse_market_instrument(symbol: str) -> Instrument:
    """Build an Instrument from a yaml entry. The smoke only exercises the
    equity path for now; adding an option case is a one-line extension."""
    return Instrument(symbol=str(symbol), asset_class=AssetClass.EQUITY)


def build_bus(cfg: AppConfig) -> Bus:
    """If `infra.bus.url` is set, use RedisStreamBus (durability + multi-process
    fan-out); otherwise fall back to InProcessBus. The downstream service and
    tools see the same Bus protocol either way."""
    bus_cfg = cfg.settings.infra.bus
    if bus_cfg.url:
        from bus import RedisStreamBus

        print(f"  [bus] RedisStreamBus → {bus_cfg.url} (stream={bus_cfg.stream!r})")
        return RedisStreamBus(
            redis_url=bus_cfg.url,
            stream=bus_cfg.stream,
            maxlen=bus_cfg.maxlen,
        )
    print("  [bus] InProcessBus (no infra.bus.url in config — set one to swap)")
    return InProcessBus()


async def bus_watcher(service: AccountService, stop: asyncio.Event) -> None:
    """Stand-in for a streaming consumer (an agent / a feature worker): print
    every account event that traverses the bus until told to stop."""
    async for ev in service.subscribe():
        print(
            f"  [bus] {ev.type.value:14s} src={ev.source:6s} "
            f"payload={type(ev.payload).__name__}"
        )
        if stop.is_set():
            break


async def fetch_market_bars(
    adapter: MarketSourcePort,
    instruments: list[Instrument],
) -> None:
    """Call get_bars on a pre-built market adapter for parsed instruments."""
    # The Port doesn't declare `feed`/`params`; they live on the concrete
    # AlpacaStockMarketAdapter. Suppress the attr-defined here so we don't
    # have to expose them on the Port just for a diagnostic print.
    print(
        f"  [market] {adapter.name} "
        f"(feed={adapter.feed}, "  # type: ignore[attr-defined]
        f"rate_limit={adapter.params.get('rate_limit', '?')})"  # type: ignore[attr-defined]
    )

    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - _BAR_LOOKBACK

    for instrument in instruments:
        print(
            f"  [market] get_bars({instrument.symbol!r}, "
            f"tf={_BAR_TIMEFRAME.value}, "
            f"start={start.isoformat()}, end={end.isoformat()})"
        )
        try:
            bars = await adapter.get_bars(instrument, _BAR_TIMEFRAME, start, end)
        except Exception as exc:  # noqa: BLE001 — surface the read-side failure
            print(f"    → raised {type(exc).__name__}: {exc}")
            continue
        print(f"    → {len(bars)} bar(s)")
        for bar in bars[:3]:
            print(
                f"      {bar.ts_open.isoformat()}  "
                f"O={bar.open} H={bar.high} L={bar.low} C={bar.close} "
                f"V={bar.volume} VWAP={bar.vwap} trades={bar.trades}"
            )
        if len(bars) > 3:
            print(f"      … ({len(bars) - 3} more)")


async def run(mode: str) -> None:
    print(f"=== smoke slice: mode={mode} ===")
    cfg = AppConfig.load("config/smoke.yaml")
    bus = build_bus(cfg)
    adapter = build_adapter(mode, cfg)
    guardrail = Guardrail([])
    service = AccountService(sources=[adapter], bus=bus, guardrail=guardrail)
    tools = ToolLayer(account=service)

    # Persistence: build Database + Writer from cfg.settings.infra.persistence.
    # Skipped cleanly when disabled or dsn is empty (so the smoke still runs
    # without storage if the config drops the block).
    db: Database | None = None
    writer: PersistenceWriter | None = None
    _ps = cfg.settings.infra.persistence
    if _ps.enabled and _ps.dsn:
        db = Database(_ps.dsn, echo=_ps.echo)
        await db.create_all()
        writer = PersistenceWriter(bus=bus, db=db)
        print(f"  [persistence] enabled (dialect={db.dialect_name}, echo={_ps.echo})")
    else:
        print("  [persistence] disabled (no dsn or enabled=false)")

    await bus.start()

    # The writer runs as a background task. It subscribes to BAR/NEWS/FILL on
    # the bus and writes each event to the DB. Cancelled at shutdown.
    # Scheduled early so the subscription is active before the service starts.
    writer_task: asyncio.Task[None] | None = None
    if writer is not None:
        writer_task = asyncio.create_task(writer.run(), name="persistence-writer")
        await asyncio.sleep(0)

    await service.start()
    print("health:", await adapter.health())

    # Market read path: build the stock market adapter from yaml + .env and
    # pull a fresh bar window. Demonstrates that the adapter is wired the
    # same way as the account adapter (config-driven, registry-resolved,
    # splat-into-ctor) and that get_bars returns schema.Bar DTOs.
    print("\n-- market: get_bars --")
    market_settings = [
        src
        for src in cfg.settings.adapters.market
        if src.enabled and src.source == "alpaca" and src.name == "stock"
    ]
    if not market_settings:
        print("  [market] no market/alpaca/stock source configured — skipping")
    else:
        market_src = market_settings[0]
        market_adapter = build_market_adapter(cfg, market_src)
        market_instruments = [
            parse_market_instrument(symbol)
            for symbol in market_src.params.get("instruments", [])
        ]
        await fetch_market_bars(market_adapter, market_instruments)

    stop = asyncio.Event()
    watcher = asyncio.create_task(bus_watcher(service, stop))

    # (a) agent-style synchronous tool calls
    print("\n-- tool: get_balance --")
    print(" ", await tools.dispatch("get_balance", {}))
    print("-- tool: get_positions --")
    print(" ", await tools.dispatch("get_positions", {}))

    print("-- tool: place_order (BUY 1 AAPL) --")
    order_res = await tools.dispatch(
        "place_order",
        {
            "client_order_id": f"client-smoke-{uuid.uuid4().hex[:8]}",
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1,
        },
    )
    print(" ", order_res)

    print("-- tool: cancel_order --")
    broker_order_id = order_res.get("broker_order_id")
    if broker_order_id:
        print(
            " ",
            await tools.dispatch(
                "cancel_order",
                {"broker_order_id": broker_order_id},
            ),
        )

    # (b) let the streamed fills flow across the bus for a moment
    print("\n-- streaming account events off the bus --")
    await asyncio.sleep(1.5)
    stop.set()
    watcher.cancel()

    # (c) drain/stop the writer first so all events are flushed to the DB before reading
    if writer_task is not None:
        writer_task.cancel()
        try:
            await writer_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"persistence writer task failed: {e}")
            raise

    # (d) show what the writer captured — reads via the public Repository
    # so the smoke is also an end-to-end check of the read face.
    if db is not None:
        repo = Repository(db)
        fills = await repo.fetch_fills()
        print(f"\n  [persistence] DB has {len(fills)} fill(s):")
        for f in fills:
            print(
                f"    - fill_id={f.fill_id} broker={f.broker_order_id} "
                f"symbol={f.instrument.symbol} qty={f.quantity} price={f.price}"
            )

    # shutdown remaining services
    await service.stop()
    await bus.stop()
    if db is not None:
        await db.close()
    print("\n=== done ===")


async def main(mode: str | None = None) -> None:
    if mode is None:
        if "pytest" in sys.modules:
            mode = "mock"
        else:
            cmd_args = sys.argv[1:]
            if not cmd_args:
                mode = "mock"
            elif len(cmd_args) == 2 and cmd_args[0] == "account":
                mode = cmd_args[1]
            elif len(cmd_args) == 1:
                mode = cmd_args[0]
            else:
                mode = "mock"
    await run(mode)


if __name__ == "__main__":
    asyncio.run(main())
