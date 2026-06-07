# ruff: noqa: E402
"""
apps/live — composition root. The ONE place wiring happens. Source COUNT never
appears here (it's read from config). Single Python process: the bus, the
services, AND the agent gateway all run on the same asyncio loop.

The thing to notice: the gateway is NOT a second process. It's served via
asyncio.gather alongside service.start(), sharing the bus and the loop. The only
*other* process is the TS Pi agent, which talks to this one over HTTP — started
separately (two terminals in dev; `just dev` later).

To split into real processes / multi-language later: swap InProcessBus ->
RedisStreamsBus and the gateway's HTTP -> gRPC. The packages/ never change.

Today's reality: only the bus, AccountService, and the gateway have working
start()/serve() implementations. MarketService, NewsService, FeatureRuntime,
FeatureService, and the in-process TraderAgentHarness are still
NotImplementedError stubs. This file BUILDS the full graph the docstring above
describes, then attempts to start each piece — anything still stubbed logs a
clear warning and is dropped from the ToolLayer so the gateway boots usable
even when only the account path is wired. As stubs get filled in, this file
needs no edits; their start() just stops raising NotImplementedError.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Path injection for local packages and namespace packages (mirrors apps/backtest).
_root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_root_dir / "packages"))
for _p in (_root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(_p))

import asyncio
import os
import signal
from typing import Any

from account import AccountService
from bus import Bus, InProcessBus
from contracts.ports import AccountSourcePort, MarketSourcePort, NewsSourcePort
from feature import FeatureService
from feature.runtime import FeatureRuntime
from guardrail import Guardrail
from loguru import logger
from market import MarketService
from news import NewsService
from observability import setup_logging
from persistence import Database, PersistenceWriter, Repository
from plugins import discover, registry
from tools import ToolLayer

from apps.live.pi_gateway import AgentGateway
from config import AppConfig

# Default port for the agent gateway. Overridable via cfg.settings.agent.gateway_port.
_DEFAULT_GATEWAY_PORT = 8787


# --- helpers ----------------------------------------------------------------
def _adapter_packages(cfg: AppConfig) -> list[str]:
    """Derive the importlib package names for every adapter named in the yaml.
    Importing these packages runs their @register decorators and populates the
    registry, which build_sources() then reads.

    Convention enforced across the repo:
        packages/adapters/<domain>/<name>/__init__.py  -> 'adapters.<domain>.<name>'
    """
    out: list[str] = []
    for domain in ("market", "news", "account"):
        for src in getattr(cfg.settings.adapters, domain):
            if src.enabled:
                out.append(f"adapters.{domain}.{src.source}")
    return out


def _feature_packages(cfg: AppConfig) -> list[str]:
    """Same idea for the feature DAG nodes:
    packages/features/<category>/<name>/__init__.py -> 'features.<category>.<name>'
    """
    out: list[str] = []
    for category, sources in cfg.settings.features.items():
        for src in sources:
            if src.enabled:
                out.append(f"features.{category}.{src.source}")
    return out


def _build_guardrail(cfg: AppConfig) -> Guardrail:
    """Translate cfg.settings.guardrails into RiskRule instances. The yaml is
    notional/dollar-denominated; the v1 rule classes (MaxQuantityRule,
    BuyingPowerRule) speak shares/buying-power. Until notional-cap rules exist,
    log what we couldn't translate and ship an empty rule list rather than
    silently misenforcing. Kill-switch state is set untripped at boot regardless
    of the flag (it's a runtime control, not a startup decision)."""
    g = cfg.settings.guardrails or {}
    unmapped = [
        k
        for k in g
        if k
        in (
            "max_drawdown_percent",
            "max_position_size_dollars",
            "max_order_size_dollars",
        )
    ]
    if unmapped:
        logger.warning(
            "guardrails: yaml fields {} have no matching RiskRule yet; "
            "shipping with no enforced rules. Wire concrete rules when the "
            "notional-cap classes land.",
            unmapped,
        )
    return Guardrail(rules=[])


def _build_bus(cfg: AppConfig) -> Bus:
    """If `infra.bus.url` is set, use RedisStreamBus (durability + multi-process
    fan-out); otherwise fall back to InProcessBus. The downstream service and
    tools see the same Bus protocol either way."""
    bus_cfg = cfg.settings.infra.bus
    if bus_cfg.url:
        from bus import RedisStreamBus

        logger.info(
            "bus: RedisStreamBus (url={}, stream={})",
            bus_cfg.url,
            bus_cfg.stream,
        )
        return RedisStreamBus(
            redis_url=bus_cfg.url,
            stream=bus_cfg.stream,
            maxlen=bus_cfg.maxlen,
        )
    logger.info("bus: InProcessBus")
    return InProcessBus()


async def _try_start(service: Any, label: str) -> bool:
    """Start a service; if its start() is still a NotImplementedError stub,
    log a warning and return False so the caller can drop it from the tool
    layer. Any other exception is fatal — let it propagate."""
    try:
        await service.start()
        logger.info("started {}", label)
        return True
    except NotImplementedError:
        logger.warning(
            "{} is not yet implemented (start() raised NotImplementedError); "
            "skipping. The gateway will boot without it.",
            label,
        )
        return False


# --- the run function -------------------------------------------------------
async def run(config_path: str = "config/live.yaml") -> None:
    """Wiring order (matches the module docstring step-for-step):
    1. cfg = AppConfig.load(config_path); discover() adapter+feature packages
    2. bus = InProcessBus()
    3. build sources from cfg via registry; build guardrail from cfg.risk
    4. account = AccountService(source, bus, guardrail)   # + market/news/feature
    5. tool_layer = ToolLayer(account, market, news, features)
    6. gateway = AgentGateway(tool_layer, bus)
    7. run bus-side and HTTP-side on the SAME loop via asyncio.gather
    8. graceful shutdown: stop services, drain bus, stop server.
    """
    # 1. config + plugin discovery
    cfg = AppConfig.load(config_path)
    discover(_adapter_packages(cfg) + _feature_packages(cfg))

    # 2. bus
    bus = _build_bus(cfg)
    await bus.start()

    # 2b. persistence (optional — live can run without storage; the writer
    #     and repository just won't exist). Skipped when disabled or when
    #     dsn is missing/empty. `create_all()` is dev-only — in prod use
    #     alembic migrations + the one-time create_hypertable() calls.
    db: Database | None = None
    persistence: PersistenceWriter | None = None
    _psettings = cfg.settings.infra.persistence
    if _psettings.enabled and _psettings.dsn:
        db = Database(_psettings.dsn, echo=_psettings.echo)
        await db.create_all()
        persistence = PersistenceWriter(bus=bus, db=db)
        logger.info(
            "persistence: enabled (dialect={}, echo={})",
            db.dialect_name,
            _psettings.echo,
        )
    else:
        logger.info(
            "persistence: disabled (enabled={}, dsn_set={})",
            _psettings.enabled,
            bool(_psettings.dsn),
        )

    persistence_task = None
    if persistence is not None:
        persistence_task = asyncio.create_task(
            persistence.run(), name="persistence-writer"
        )
        # Yield to the event loop so the writer task runs and registers its subscription immediately
        await asyncio.sleep(0)

    # 3. sources + guardrail
    market_sources = registry.build_sources(
        "market", cfg.enabled_sources("market"), as_=MarketSourcePort
    )
    news_sources = registry.build_sources(
        "news", cfg.enabled_sources("news"), as_=NewsSourcePort
    )
    account_sources = registry.build_sources(
        "account", cfg.enabled_sources("account"), as_=AccountSourcePort
    )
    if not account_sources:
        raise RuntimeError(
            "No enabled account source in config — live cannot boot without "
            "an account to route place_order through. Edit config/live.yaml."
        )
    guardrail = _build_guardrail(cfg)

    # 4. services (always built; start may be a stub today — we'll attempt
    #    start below and drop the unstarted ones from the tool layer)
    account_service = AccountService(
        sources=account_sources, bus=bus, guardrail=guardrail
    )
    market_service = (
        MarketService(
            sources=market_sources,
            bus=bus,
            repository=Repository(db) if db is not None else None,
            writer=persistence.writer if persistence is not None else None,
        )
        if market_sources
        else None
    )
    news_service = NewsService(sources=news_sources, bus=bus) if news_sources else None

    feature_service: FeatureService | None = None
    feature_runtime: FeatureRuntime | None = None
    feature_processors = registry.build_processors(cfg.enabled_features())
    if feature_processors:
        feature_runtime = FeatureRuntime(bus=bus)
        # add_processor is a NotImplementedError stub today; try once, log on fail.
        for p in feature_processors:
            try:
                feature_runtime.add_processor(p)
            except NotImplementedError:
                logger.warning(
                    "FeatureRuntime.add_processor is not implemented; "
                    "feature DAG will not be wired."
                )
                feature_runtime = None
                break
        if feature_runtime is not None:
            feature_service = FeatureService(runtime=feature_runtime)

    # Try to start each non-account service; on NotImplementedError, drop it.
    started_services: list[Any] = [account_service]  # always required
    await _try_start(account_service, "AccountService")

    if market_service is not None:
        if await _try_start(market_service, "MarketService"):
            started_services.append(market_service)
        else:
            market_service = None

    if news_service is not None:
        if await _try_start(news_service, "NewsService"):
            started_services.append(news_service)
        else:
            news_service = None

    if feature_runtime is not None:
        if await _try_start(feature_runtime, "FeatureRuntime"):
            started_services.append(feature_runtime)
        else:
            feature_runtime = None
            feature_service = None

    # 5. tool layer — None for any service that didn't make it past start()
    tools = ToolLayer(
        account=account_service,
        market=market_service,
        news=news_service,
        features=feature_service,
    )

    # 6. gateway
    gateway = AgentGateway(tool_layer=tools, bus=bus)
    gateway_port = int(cfg.settings.agent.get("gateway_port", _DEFAULT_GATEWAY_PORT))
    gateway_host = cfg.settings.agent.get("gateway_host", "127.0.0.1")
    logger.info(
        "Trader live up: account={} market={} news={} features={} gateway=http://{}:{}",
        account_sources[0].name,
        bool(market_service),
        bool(news_service),
        bool(feature_service),
        gateway_host,
        gateway_port,
    )

    # 7. run forever on this loop. The gateway.serve() coroutine drives uvicorn
    #    with loop="none", so it shares the loop with the started services.
    #    AccountService.start() already returned (it spun off its pump task); we
    #    keep that task alive implicitly because cancelling stop_event below
    #    triggers our own shutdown path, which stop()s each service.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows / non-default loops: fall back to KeyboardInterrupt only.
            pass

    serve_task = asyncio.create_task(
        gateway.serve(host=gateway_host, port=gateway_port),
        name="agent-gateway",
    )
    stop_task = asyncio.create_task(stop_event.wait(), name="stop-signal")

    # NOTE: we use asyncio.wait + FIRST_COMPLETED, not asyncio.gather.
    # `gather` would wait for ALL tasks to finish and return a list of
    # results — wrong semantic. We want "first to finish ends the loop"
    # (stop signal, gateway crash, or writer crash). The trade-off is
    # that `wait` does NOT observe task exceptions for you: a task that
    # raised lands in `done` with an unobserved exception unless we call
    # `task.exception()` explicitly. The crash-detection block below
    # (for both serve_task and writer_task) is what surfaces those.
    # Don't refactor this to gather without re-doing that.

    live_tasks: set[asyncio.Task[Any]] = {serve_task, stop_task}
    if persistence_task is not None:
        live_tasks.add(persistence_task)

    # First of the live_tasks to finish ends the live loop:
    #   serve_task finishing means uvicorn crashed or exited cleanly;
    #   stop_task finishing means SIGINT/SIGTERM was received;
    #   persistence_task finishing means the persistence bus consumer raised
    #     (it runs forever otherwise — cancellation in shutdown is the
    #     only normal exit).
    done, pending = await asyncio.wait(
        live_tasks,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # 8. graceful shutdown
    logger.info("shutting down...")
    for t in pending:
        t.cancel()
    for t in pending:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    # Re-raise a serve_task failure so the operator sees why we died.
    #
    # WARNING: `asyncio.wait` does NOT auto-observe task exceptions. A task
    # that raised and was returned in `done` still has an unobserved
    # exception until either `task.exception()` is called (as below) or
    # the task object is GC'd. Skipping this block means the crash is
    # silently swallowed — the only signal would be a "Task exception
    # was never retrieved" warning at GC time, which is easy to miss
    # in production logs. Keep this block symmetric: every task added
    # to `live_tasks` above needs an `in done` check here.
    if serve_task in done:
        exc = serve_task.exception()
        if exc is not None:
            logger.exception("gateway serve crashed: {}", exc)
            raise exc
    if persistence_task is not None and persistence_task in done:
        exc = persistence_task.exception()
        if exc is not None:
            logger.exception("persistence writer crashed: {}", exc)
            raise exc

    # Stop services in reverse start order: agent-facing first, account last so
    # the order path stays alive until everything reading from it is gone.
    for svc in reversed(started_services):
        name = type(svc).__name__
        try:
            await svc.stop()
            logger.info("stopped {}", name)
        except NotImplementedError:
            # stop() may be a stub even when start() worked; nothing to do.
            pass
        except Exception:
            logger.exception("error stopping {}", name)

    await bus.stop()
    if db is not None:
        await db.close()
        logger.info("persistence: closed")
    logger.info("Trader live: shutdown complete.")


def main() -> None:
    setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
    cfg_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("CONFIG_PATH", "config/live.yaml")
    )
    try:
        asyncio.run(run(cfg_path))
    except KeyboardInterrupt:
        # Belt-and-braces: signal handlers above handle SIGINT cleanly; this
        # catches the path where add_signal_handler wasn't available.
        logger.info("KeyboardInterrupt — exiting.")


if __name__ == "__main__":
    main()
