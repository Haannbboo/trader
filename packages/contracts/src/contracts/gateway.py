"""
gateway — Pydantic models for the AgentGateway HTTP seam.

These are the data shapes that cross the HTTP boundary exposed by
apps/live/pi_gateway.py. They live here (in contracts/) so the contract
generator in scripts/generate_contracts.py can emit JSON Schemas from a
single source of truth that both Python and TypeScript import.

`ToolSpec` is the wire format for one entry in `GET /tools`. `DispatchRequest`
is the request body for `POST /dispatch`. `BusEvent` is the SSE data line on
`GET /stream` — the generic `Event[PayloadT]` is flattened to a non-generic
shape because JSON Schema has no generics, and the TS client treats the
payload as a free-form `Record<string, unknown>` anyway.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contracts.schema import EventType


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


class BusEvent(BaseModel):
    """Envelope that flows over `GET /stream` as one SSE `data:` line per
    occurrence. Generic over payload in Python (`Event[Quote]`, `Event[Fill]`,
    ...) but flattened here because JSON Schema has no generics and the TS
    client treats the payload as `Record<string, unknown>` — it discriminates
    on `type` and parses the payload lazily."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: EventType
    source: str
    payload: dict[str, Any]
    ts_event: datetime
    ts_ingest: datetime
    event_id: UUID
    seq: Optional[int] = None
