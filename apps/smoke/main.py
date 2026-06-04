# ruff: noqa: E402
"""
apps/smoke/main.py — the thin vertical slice, runnable.

Proves the contract end-to-end: build a bus, inject ONE account adapter into the
minimal AccountService, start the event pump, then (a) call tools synchronously
like an agent would and (b) watch fills stream off the bus.

Run:
    python -m apps.smoke.main mock        # no network, fake data
    python -m apps.smoke.main alpaca      # real Alpaca paper account

The ONLY difference between the two modes is which adapter is built. Everything
downstream (service, bus, tools) is identical — that substitutability is the
thing this slice exists to demonstrate.

The bus impl is also driven by config: if `infra.bus.url` is set in
config/smoke.yaml the slice uses RedisStreamBus (start `just up` first); if
not, it falls back to InProcessBus. That's the same swap-in-place story, one
layer up.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Path injection for local packages and namespace packages
root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(root_dir / "packages"))
for p in (root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(p))

import asyncio

from account import AccountService  # pyrefly: ignore [missing-import]
from bus import InProcessBus, RedisStreamBus  # pyrefly: ignore [missing-import]
from contracts import AccountSourcePort
from guardrail import Guardrail  # pyrefly: ignore [missing-import]
from persistence import (
    Database,
    PersistenceWriter,
    Repository,
)  # pyrefly: ignore [missing-import]
from tools import ToolLayer  # pyrefly: ignore [missing-import]

from apps.smoke.mock_adapter import MockAccountAdapter
from config import AppConfig  # pyrefly: ignore [missing-import]


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


def build_bus(cfg: AppConfig) -> InProcessBus | RedisStreamBus:
    """If `infra.bus.url` is set, use RedisStreamBus (durability + multi-process
    fan-out); otherwise fall back to InProcessBus. The downstream service and
    tools see the same Bus protocol either way."""
    bus_cfg = cfg.settings.infra.bus
    if bus_cfg.url:
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
    await service.start()
    print("health:", await adapter.health())

    # The writer runs as a background task. It subscribes to BAR/NEWS/FILL on
    # the bus and writes each event to the DB. Cancelled at shutdown.
    writer_task: asyncio.Task[None] | None = None
    if writer is not None:
        writer_task = asyncio.create_task(writer.run(), name="persistence-writer")

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

    # (c) show what the writer captured — reads via the public Repository
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

    # shutdown order: writer first (cancel the bus consumer), then service,
    # then bus, then db. Cancelling the writer before the bus is closed
    # means the writer's in-flight `bus.subscribe` raises CancelledError
    # cleanly instead of ConnectionError.
    if writer_task is not None:
        writer_task.cancel()
        try:
            await writer_task
        except (asyncio.CancelledError, Exception):
            pass
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
