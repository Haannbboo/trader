"""
gateway — Pydantic models for the AgentGateway HTTP seam.

These are the data shapes that cross the HTTP boundary exposed by
apps/live/pi_gateway.py. They are the single source of truth for the
cross-language wire contract; the TypeScript side (`packages/tool-client/`)
hand-writes matching types in `src/types.ts`, and a Python guard test
asserts the real gateway responses validate against these models so
drift in either direction gets caught.

`ToolSpec` is the wire format for one entry in `GET /tools`. `DispatchRequest`
is the request body for `POST /dispatch`. `BusEvent` is the SSE data line on
`GET /stream` and is intentionally an ALIAS for the generic `Event[dict]`
defined in contracts/schema.py — it is NOT a second definition that could
drift.

See docs/adr/0002-contracts-strategy.md for the rationale on the
Python-SOT + TS-mirror approach.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contracts.schema import Event


class ToolSpec(BaseModel):
    """One tool the agent can invoke. Returned by `GET /tools` as a JSON array.

    `parameters` is a free-form JSON Schema object describing the tool's args.
    The TS client converts it to a TypeBox schema at runtime for the
    `AgentTool.parameters` field; Python's `ToolLayer.dispatch()` validates
    the same args dict against this schema via simple structural checks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class DispatchRequest(BaseModel):
    """Wire envelope for `POST /dispatch`. `args` is a free-form dict — the tool
    layer validates per-tool inside `dispatch()`; this model only guarantees
    the outer JSON shape and lets FastAPI return 422 on a malformed body."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)


# `BusEvent` is the SSE wire shape. It is a parameterized generic over the
# real `Event` envelope from contracts.schema — the same fields, the same
# `EventType` enum, the same frozen + extra="forbid" rules. Binding the
# payload to `dict[str, Any]` is what keeps the wire shape language-neutral
# without baking in any concrete Quote/Fill/NewsItem shape.
BusEvent = Event[dict[str, Any]]  # type: ignore[valid-type]

