"""
apps.live.pi_gateway — the THIN cross-language seam. Exposes the ToolLayer over
HTTP so a TS/Go/whatever agent can drive the Python system. Contains NO business
logic: it only translates HTTP <-> ToolLayer. The ToolLayer (and everything
under it) does not know it's being exposed over HTTP — same principle as an
adapter not knowing who calls it. Swap HTTP for gRPC later by replacing THIS
file; ToolLayer never changes.

Runs IN-PROCESS with the bus/services (same asyncio loop) — it is not a separate
process. apps/live wires it and serves it alongside the services.

Three endpoints, mirroring the two interaction kinds the system already has:
    GET  /tools     -> tool_layer.tool_specs()       (command/query catalog)
    POST /dispatch  -> tool_layer.dispatch(name,args)(one tool call, req-resp)
    GET  /stream    -> SSE of bus events             (push stream to the agent)

Error mapping at the seam (the only policy decision in this file):
    RiskRejected -> 400 {"error":"risk_rejected","reason":..., "rule":...}
    ValueError   -> 400 {"error":"bad_request",   "reason":...}
    other        -> 500 (logged via loguru; body is FastAPI's default)

Everything crossing this seam is JSON-able and language-neutral on purpose, so
the tool/event schemas can later move into a shared contracts/ dir feeding every
language. Nothing here is Python-private.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator

from contracts.gateway import DispatchRequest  # pyrefly: ignore [missing-import]
from contracts.ports import Bus, Subscription  # pyrefly: ignore [missing-import]
from contracts.schema import (
    AssetClass,
    EventType,
    Instrument,
)  # pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from guardrail import RiskRejected  # pyrefly: ignore [missing-import]
from loguru import logger

if TYPE_CHECKING:
    import socket

    # ToolLayer is a structural dependency; typed loosely to avoid import coupling.


# 15s sits below typical reverse-proxy idle timeouts (nginx 60s, ALB 60s,
# Cloudflare 100s). Anything longer risks silent connection death behind a proxy.
_HEARTBEAT_SECONDS = 15.0


# Uvicorn's loggers are installed exactly once per process. Reinstalling the
# intercept handler on every serve() call would stack handlers and duplicate
# every line. Guard with a module-level flag.
_uvicorn_logging_routed = False


def _route_uvicorn_logging_to_loguru() -> None:
    """Replace uvicorn's stdlib log handlers with a loguru intercept so access
    + error lines flow through the project's loguru sinks and formatting.
    Idempotent: subsequent calls are no-ops."""
    global _uvicorn_logging_routed
    if _uvicorn_logging_routed:
        return

    import logging

    class _InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # Translate stdlib level name -> loguru level; fall back to numeric.
            try:
                level: str | int = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            # Walk back through frames originating in `logging` itself so the
            # logged file/line points at the caller in uvicorn, not the handler.
            # This is the loguru-recommended InterceptHandler idiom — see
            # https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
            frame = logging.currentframe()
            depth = 0
            while frame and (
                depth == 0 or frame.f_code.co_filename == logging.__file__
            ):
                frame = frame.f_back
                depth += 1
            logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )

    handler = _InterceptHandler()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
    _uvicorn_logging_routed = True


class AgentGateway:
    def __init__(self, tool_layer, bus: Bus) -> None:
        """Holds the already-wired ToolLayer + bus. Builds them NOT here — apps/live
        constructs the object graph and injects it, exactly like everything else."""
        self._tools = tool_layer
        self._bus = bus

    def app(self) -> "FastAPI":
        """Build the FastAPI app with the three routes bound to this instance.
        Kept as a method so the gateway owns its routing; apps/live just serves it.

        We use add_api_route rather than the @app.get(...) decorator form so the
        routes are bound per-instance at call time (re-buildable for tests, no
        module-scope capture). Bound methods drop `self` from their signature,
        so FastAPI's signature introspection treats them as free functions."""
        app = FastAPI(title="Trader Agent Gateway", version="0.1.0")
        app.add_api_route(
            "/tools",
            self.get_tools,
            methods=["GET"],
            response_model=list[dict],
        )
        app.add_api_route(
            "/dispatch",
            self.post_dispatch,
            methods=["POST"],
            response_model=dict,
        )
        app.add_api_route(
            "/stream",
            self.get_stream,
            methods=["GET"],
            response_class=StreamingResponse,
        )
        return app

    # --- route handlers (thin translators) -----------------------------------
    async def get_tools(self) -> list[dict]:
        """GET /tools -> the agent-facing tool catalog. Pi fetches this once at
        startup to generate its tool definitions dynamically (so adding a Python
        tool needs no TS change)."""
        return self._tools.tool_specs()

    async def post_dispatch(self, body: DispatchRequest) -> dict:
        """POST /dispatch {name, args} -> run one tool call, return JSON result.
        This is the request-response channel (get_balance, place_order, ...).
        place_order still goes through AccountService -> guardrail on the Python
        side; the HTTP caller cannot bypass that. Error mapping is the only
        cross-boundary policy here: RiskRejected -> 4xx so the agent can react
        to a denied order without parsing a traceback."""
        try:
            return await self._tools.dispatch(body.name, body.args)
        except RiskRejected as e:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "risk_rejected",
                    "reason": e.reason,
                    "rule": e.rule,
                },
            )
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "reason": str(e)},
            )
        except Exception:
            # Log the traceback then re-raise so FastAPI returns 500. We do not
            # leak the internal message into the response body.
            logger.exception("dispatch failed: name={} args={}", body.name, body.args)
            raise

    async def get_stream(
        self,
        events: str = Query(
            "",
            description="Comma-separated EventType values; empty = all.",
        ),
        symbols: str = Query(
            "",
            description=(
                "Comma-separated symbols; empty = all instruments. "
                "v1 assumes EQUITY asset class for all symbols."
            ),
        ),
        sources: str = Query(
            "",
            description="Comma-separated source names; empty = all.",
        ),
    ) -> StreamingResponse:
        """GET /stream -> Server-Sent Events of bus activity (fills, quotes, news)
        for the agent to consume as context. Separate channel from /dispatch
        because this is push, not req-resp — mirrors the system's internal
        'commands via RPC, events via bus' split, extended across the process
        boundary.

        Query params mirror Subscription's three match dimensions (events,
        instruments, sources). Empty string = match-all, matching the
        Subscription protocol's empty-tuple semantics.

        Wire format: each event is a single SSE 'data:' line containing
        Event.model_dump_json(). A ': keepalive' comment line is emitted every
        ~15s to defeat reverse-proxy idle timeouts; per the SSE spec, lines
        starting with ':' are comments that clients ignore.

        On client disconnect uvicorn cancels the generator; the finally clause
        closes the bus receive stream so future publishes drop silently rather
        than queue forever."""
        sub = self._parse_subscription(events, symbols, sources)
        return StreamingResponse(
            self._sse_generator(sub),
            media_type="text/event-stream",
            headers={
                # 'no-cache' on the response; 'X-Accel-Buffering: no' tells
                # nginx not to buffer the stream. Both are SSE hygiene.
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # --- internals -----------------------------------------------------------
    def _parse_subscription(
        self, events: str, symbols: str, sources: str
    ) -> Subscription:
        """Translate the /stream query-string triple into a typed Subscription.
        Bad enum values raise ValueError, which FastAPI turns into a 422 before
        the generator ever runs — the right place to fail on a typo."""
        et: tuple[EventType, ...] = ()
        if events:
            et = tuple(EventType(v.strip()) for v in events.split(",") if v.strip())
        ins: tuple[Instrument, ...] = ()
        if symbols:
            # v1: equity-only. Add a parallel ?asset_class= param (or a
            # SYMBOL@CLASS shorthand) when options/futures are wired.
            ins = tuple(
                Instrument(symbol=s.strip(), asset_class=AssetClass.EQUITY)
                for s in symbols.split(",")
                if s.strip()
            )
        src: tuple[str, ...] = ()
        if sources:
            src = tuple(s.strip() for s in sources.split(",") if s.strip())
        return Subscription(event_types=et, instruments=ins, sources=src)

    async def _sse_generator(self, sub: Subscription) -> AsyncIterator[bytes]:
        """Async generator backing StreamingResponse: subscribe the bus, yield
        SSE data lines, heartbeat on a 15s timer, close the stream on exit.

        asyncio.timeout (3.11+) is preferred over asyncio.wait_for: on timeout
        it just raises TimeoutError without injecting CancelledError into the
        inner awaitable, leaving the anyio receive stream usable on the next
        iteration. wait_for cancels the inner await, which can leave the
        stream in a half-closed state.

        anext(stream) is equivalent to stream.__anext__() today; using the
        built-in keeps the loop body terse and forward-compatible if anyio
        ever exposes a recv()-style method we'd want to swap in."""
        stream = self._bus.subscribe(sub)
        try:
            while True:
                try:
                    async with asyncio.timeout(_HEARTBEAT_SECONDS):
                        event = await anext(stream)
                    yield f"data: {event.model_dump_json()}\n\n".encode()
                except (asyncio.TimeoutError, TimeoutError):
                    yield b": keepalive\n\n"
                except StopAsyncIteration:
                    # Upstream bus closed cleanly. End the SSE stream.
                    break
        finally:
            # MemoryObjectReceiveStream.close() is sync and idempotent. After
            # closing, the bus's matching send stream raises ClosedResourceError
            # on its next send_nowait, which inprocess.py already swallows —
            # so the subscriber row goes effectively deaf.
            if hasattr(stream, "close"):
                # RedisStreamBus returns a generator with no close(); only close in-process streams.
                try:
                    stream.close()  # type: ignore[attr-defined]
                except Exception:
                    logger.exception("failed to close bus subscription stream")

    async def serve(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8787,
        sockets: "list[socket.socket] | None" = None,
    ) -> None:
        """Run the HTTP server on the CURRENT event loop (uvicorn.Server.serve()),
        so it coexists with the bus/services rather than taking over. apps/live
        calls this inside its asyncio.gather alongside service.start().

        Two Config flags carry the weight:
          loop="none"    — uvicorn uses the loop we're already running under
                           rather than spinning up its own via asyncio.run().
                           This is the option that makes in-process co-tenancy
                           with the bus/services possible at all.
          lifespan="off" — we don't define FastAPI startup/shutdown handlers
                           (services are owned by apps/live), and disabling
                           lifespan avoids a known deadlock seen in some
                           uvicorn 0.27.x releases when loop="none" is set.

        Log routing: uvicorn writes through Python's stdlib `logging` module
        (loggers "uvicorn", "uvicorn.error", "uvicorn.access"). The project's
        sinks are loguru, so without an intercept those lines either vanish or
        come out in stdlib format. We install a minimal InterceptHandler on
        those three loggers so access lines flow through loguru with the rest
        of the app's formatting. log_config=None keeps uvicorn from reinstalling
        its own stdlib handlers on startup and clobbering ours.

        Graceful shutdown: when asyncio.gather is cancelled (SIGINT in apps/live),
        this coroutine is cancelled. uvicorn.Server.serve() translates that into
        a clean stop — should_exit flips, in-flight requests drain. We install no
        signal handlers (the outer loop owns them) and call no shutdown()
        explicitly (the cancellation does it)."""
        import uvicorn  # local import: uvicorn pulls uvloop/httptools etc.

        _route_uvicorn_logging_to_loguru()

        config = uvicorn.Config(
            self.app(),
            host=host,
            port=port,
            loop="none",
            lifespan="off",
            log_level="info",
            access_log=True,
            log_config=None,
        )
        server = uvicorn.Server(config)
        # `sockets=` is a Server.serve() parameter, not a Config parameter.
        # When provided, uvicorn skips host/port bind and accepts() on the
        # pre-bound fds — used by the e2e harness to publish a port that is
        # already reserved (no race between advertise() and bind()).
        await server.serve(sockets=sockets)
