#!/usr/bin/env python3
"""
Minimal AgentGateway harness for the tools-client end-to-end test.

Boots:
  - a stub AccountService (so /tools returns account tools and /dispatch works)
  - an InProcessBus (so /stream can be exercised too)
  - a ToolLayer combining them
  - an AgentGateway served on 127.0.0.1:<random-free-port>

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
from contracts.schema import Balance, Order, OrderStatus  # noqa: E402
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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main() -> None:
    port = free_port()
    bus = InProcessBus()
    await bus.start()
    tool_layer = ToolLayer(account=StubAccount())
    gateway = AgentGateway(tool_layer=tool_layer, bus=bus)
    # The TS test reads this line to discover the port. Flush so the test
    # doesn't have to wait on stdio buffering.
    print(json.dumps({"port": port}), flush=True)
    try:
        await gateway.serve(host="127.0.0.1", port=port)
    finally:
        await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
