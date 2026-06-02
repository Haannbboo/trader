"""
Integration test for apps/live/pi_gateway.py.

Uses fastapi.testclient.TestClient over the real AgentGateway wired against a
mock-account stack — same pieces apps/smoke/main.py uses. Covers the three
endpoints' happy paths plus the two 4xx error mappings.

SSE (/stream) is NOT exercised here: TestClient runs the app in its own thread
and event loop, so publishing events to the bus from the test thread crosses
loops in a way anyio memory streams don't tolerate. Verify SSE manually with
`curl -N http://127.0.0.1:8787/stream?events=fill` once main.py is wired.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from account import AccountService
from apps.live.pi_gateway import AgentGateway
from apps.smoke.mock_adapter import MockAccountAdapter
from bus import InProcessBus
from contracts.gateway import DispatchRequest, ToolSpec
from contracts.schema import Order
from guardrail import Guardrail, RiskRejected, RiskRule, RuleResult
from tools import ToolLayer


# --- helpers ----------------------------------------------------------------
class _AlwaysReject:
    """A RiskRule that rejects every order — used to exercise the 4xx mapping."""

    name = "always_reject"

    def evaluate(self, order: Order, ctx) -> RuleResult:  # noqa: D401
        return RuleResult(approved=False, reason="testing rejection")


def _build_gateway(*, rules: list[RiskRule] | None = None) -> AgentGateway:
    bus = InProcessBus()
    adapter = MockAccountAdapter(n_fills=0, interval_s=0.0)
    guardrail = Guardrail(rules=rules or [])
    service = AccountService(sources=[adapter], bus=bus, guardrail=guardrail)
    tools = ToolLayer(account=service)
    return AgentGateway(tool_layer=tools, bus=bus)


def _place_order_args() -> dict:
    return {
        "client_order_id": "test-001",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": "1",
    }


# --- tests ------------------------------------------------------------------
def test_get_tools_lists_account_tools() -> None:
    """GET /tools returns the catalog; account tools are always present."""
    client = TestClient(_build_gateway().app())
    r = client.get("/tools")
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()}
    assert {"get_balance", "get_positions", "place_order", "cancel_order"} <= names


def test_dispatch_get_balance_round_trip() -> None:
    """POST /dispatch get_balance returns the mock Balance as JSON."""
    client = TestClient(_build_gateway().app())
    r = client.post("/dispatch", json={"name": "get_balance"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Balance fields are serialized via Pydantic's model_dump(mode="json").
    assert {"cash", "equity", "buying_power", "ts_event", "currency"} <= set(body)


def test_dispatch_place_order_happy_path() -> None:
    """POST /dispatch place_order routes through guardrail([]) and returns
    the broker-populated order."""
    gateway = _build_gateway()
    # Bus must be started so AccountService.place_order's post-publish doesn't
    # warn-and-drop. The publish itself is fire-and-forget; we just need it to
    # not raise.
    asyncio.run(gateway._bus.start())  # type: ignore[attr-defined]

    client = TestClient(gateway.app())
    r = client.post(
        "/dispatch",
        json={"name": "place_order", "args": _place_order_args()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["broker_order_id"] == "mock-broker-1"
    assert body["status"] == "filled"
    assert Decimal(body["filled_quantity"]) == Decimal("1")


def test_dispatch_unknown_tool_returns_400_bad_request() -> None:
    """Unknown tool name -> ValueError -> 400 with error=bad_request."""
    client = TestClient(_build_gateway().app())
    r = client.post("/dispatch", json={"name": "no_such_tool"})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "bad_request"
    assert "no_such_tool" in detail["reason"]


def test_dispatch_risk_rejected_returns_400_with_reason_and_rule() -> None:
    """A rule that rejects every order surfaces as 400 with the rule name and
    the reason — Pi must see why an order was blocked, not a traceback."""
    gateway = _build_gateway(rules=[_AlwaysReject()])
    asyncio.run(gateway._bus.start())  # type: ignore[attr-defined]

    client = TestClient(gateway.app())
    r = client.post(
        "/dispatch",
        json={"name": "place_order", "args": _place_order_args()},
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail == {
        "error": "risk_rejected",
        "reason": "testing rejection",
        "rule": "always_reject",
    }


def test_dispatch_malformed_body_returns_422() -> None:
    """Missing the required `name` field -> Pydantic validation -> 422 before
    the handler runs. (Sanity check on the DispatchRequest model wiring.)"""
    client = TestClient(_build_gateway().app())
    r = client.post("/dispatch", json={"args": {}})
    assert r.status_code == 422, r.text


def test_stream_endpoint_is_registered() -> None:
    """Prove /stream is wired without actually opening an SSE connection.
    We don't drive an SSE request through TestClient because the ASGI loop
    inside TestClient does not reliably forward a client-disconnect to our
    generator on context-manager exit, so the generator blocks on its 15s
    heartbeat. Verify SSE manually with
        curl -N http://127.0.0.1:8787/stream?events=fill
    once main.py is wired."""
    app = _build_gateway().app()
    paths = {r.path: r for r in app.routes}  # type: ignore[attr-defined]
    assert "/stream" in paths
    route = paths["/stream"]
    assert "GET" in route.methods  # type: ignore[attr-defined]
    # Bonus: the handler should declare the three filter params so the
    # query-string contract stays the documented one.
    handler = route.endpoint  # type: ignore[attr-defined]
    import inspect

    params = set(inspect.signature(handler).parameters)
    assert {"events", "symbols", "sources"} <= params


# Direct check that RiskRejected is what we map, in case the import path
# changes silently:
def test_riskrejected_is_importable_and_carries_expected_fields() -> None:
    e = RiskRejected("nope", rule="some_rule")
    assert e.reason == "nope"
    assert e.rule == "some_rule"
    with pytest.raises(RiskRejected):
        raise e


# --- contract guard tests ---------------------------------------------------
# These tests are the only enforcement that the gateway's wire shape stays in
# sync with the Pydantic models in `contracts.gateway`. If a Python dev adds
# a required field to `ToolSpec` without updating the gateway (or the gateway
# starts returning a field that's not in the model), these tests fail and
# flag the drift. The TS side (packages/tool-client/src/types.ts) mirrors
# these shapes by hand and should be updated in the same commit.
#
# See docs/adr/0002-contracts-strategy.md for why we don't generate the TS
# types from these models.
def test_get_tools_response_conforms_to_tool_spec_model() -> None:
    """Every item in the /tools response MUST validate against `ToolSpec`.
    Catches two drift directions:
      1. A new REQUIRED field is added to ToolSpec but the gateway doesn't
         return it (validation fails on the missing field).
      2. The gateway starts returning a field that's not in ToolSpec
         (ToolSpec has extra='forbid', so validation fails on the unknown
         field).
    Either failure is a signal to either update ToolSpec (SOT change) or
    update the gateway + the TS hand-written types in lockstep."""
    client = TestClient(_build_gateway().app())
    r = client.get("/tools")
    assert r.status_code == 200, r.text
    items = r.json()
    assert items, "catalog should not be empty for a gateway with the account stack"

    for item in items:
        # `model_validate` raises ValidationError on any shape mismatch.
        # We don't assert on the parsed value's contents here — that's the
        # job of the other tests. This test is purely a shape conformance
        # check between the wire and the SOT model.
        ToolSpec.model_validate(item)


def test_dispatch_request_body_conforms_to_dispatch_request_model() -> None:
    """A canonical /dispatch request body MUST validate against `DispatchRequest`
    (the model FastAPI uses at the route boundary). If the request shape
    changes here, the corresponding TS type in packages/tool-client/src/types.ts
    needs to change too — this test is the canary that fires first."""
    body = {"name": "get_balance", "args": {}}
    parsed = DispatchRequest.model_validate(body)
    assert parsed.name == "get_balance"
    assert parsed.args == {}
