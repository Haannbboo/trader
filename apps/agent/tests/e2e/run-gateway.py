#!/usr/bin/env python3
"""
Minimal AgentGateway harness for the tool-client end-to-end test.

Boots:
  - a stub AccountService (so /tools returns account tools and /dispatch works)
  - an InProcessBus (so /stream can be exercised too)
  - a ToolLayer combining them
  - an AgentGateway served on 127.0.0.1:<random-free-port>
  - a background task that publishes sample events to the bus on a 500ms
    cadence, so the e2e test's /stream subscriber has something to consume

Prints the port as JSON to stdout ONCE bound, then blocks serving uvicorn
until killed (SIGTERM from the test runner's afterAll). The TS test reads
the port line, polls until the socket is live, runs the forwarder, then
sends SIGTERM to clean up.

Mirror of tests/conftest.py: the packages' `src/` dirs aren't installed
into site-packages, so we add them to sys.path here. Without this dance
`from tools import ToolLayer` fails with ModuleNotFoundError.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# conftest-equivalent sys.path setup
REPO = Path(__file__).resolve().parents[4]  # .../tests/e2e/run-gateway.py -> repo
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages"))
for _src in (REPO / "packages").glob("**/src"):
    sys.path.insert(0, str(_src))

from apps.live.pi_gateway import AgentGateway  # noqa: E402
from bus.inprocess import InProcessBus  # noqa: E402
from contracts.gateway import BusEvent  # noqa: E402
from contracts.schema import Balance, EventType, Order, OrderStatus  # noqa: E402
from tools import ToolLayer  # noqa: E402


class StubAccount:
    """Stub AccountService — implements just enough for /tools + /dispatch."""

    async def get_balance(self) -> Balance:
        return Balance(
            cash=Decimal("1000"),
            equity=Decimal("1500"),
            buying_power=Decimal("2000"),
            ts_event=datetime.now(timezone.utc),
        )

    async def get_positions(self) -> list:
        return []

    async def get_orders(self) -> list:
        return []

    async def place_order(self, order: Order) -> Order:
        return order.model_copy(
            update={"status": OrderStatus.NEW, "broker_order_id": "stub-1"}
        )

    async def cancel_order(self, broker_order_id: str) -> None:
        return None


def bind_ephemeral_port() -> tuple[socket.socket, int]:
    """Pre-bind a listening socket on 127.0.0.1:<kernel-chosen> and return it
    alongside the resolved port. The kernel-assigned port is reserved from the
    moment of bind(), so the TS test's poll loop can't race another process
    into claiming it before uvicorn starts accepting.

    The caller owns the socket and passes it to AgentGateway.serve(sockets=[sock])
    so uvicorn accept()s on this exact fd — no second bind. uvicorn closes the
    socket on shutdown."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(128)  # uvicorn's default backlog
    return sock, port


async def _publish_samples(bus: InProcessBus) -> None:
    """Publish a `BusEvent`-shaped fill to the bus every 500ms, forever (until
    cancelled). Gives the e2e test's /stream subscriber a moving target to
    latch onto without timing pressure on the connect path."""
    i = 0
    while True:
        await asyncio.sleep(0.5)
        event = BusEvent(
            type=EventType.FILL,
            source="harness",
            payload={"sequence": i, "symbol": "AAPL", "side": "buy", "quantity": "10"},
            ts_event=datetime.now(timezone.utc),
        )
        try:
            await bus.publish(event)
        except Exception:
            # Bus is shutting down; cancellation will fire next iteration.
            return
        i += 1


async def main() -> None:
    sock, port = bind_ephemeral_port()
    bus = InProcessBus()
    await bus.start()
    tool_layer = ToolLayer(account=StubAccount())
    gateway = AgentGateway(tool_layer=tool_layer, bus=bus)
    # The TS test reads this line to discover the port. By this point the
    # port is already bound to `sock` and reserved, so advertising it is
    # race-free: the only thing that can accept on that port is `sock`.
    # Flush so the test doesn't have to wait on stdio buffering.
    print(json.dumps({"port": port}), flush=True)

    # Background publisher: see _publish_samples docstring. Cancellation
    # flows through bus.stop() in the finally block below.
    publisher = asyncio.create_task(_publish_samples(bus))

    try:
        # Pass the pre-bound listening socket to uvicorn via serve(sockets=...)
        # so uvicorn doesn't re-bind (which would lose the kernel-assigned port
        # to TIME_WAIT if a previous harness crashed on the same port).
        await gateway.serve(host="127.0.0.1", port=port, sockets=[sock])
    finally:
        publisher.cancel()
        try:
            await publisher
        except asyncio.CancelledError:
            pass
        await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
