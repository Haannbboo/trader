# contracts/

Language-neutral JSON Schemas for the cross-language HTTP seam (the
AgentGateway in `apps/live/pi_gateway.py`).

**Source of truth:** Python. The Pydantic models in
`packages/contracts/src/contracts/gateway.py` are the canonical definitions.
This directory is **generated output** — re-run `just gen-contracts` whenever
the models change, and let `just gen-contracts-check` fail in CI if a
contributor forgot to regenerate.

| File                  | Wire shape                                      |
| --------------------- | ----------------------------------------------- |
| `tools.schema.json`   | `GET /tools` response — array of `ToolSpec`     |
| `dispatch.schema.json`| `POST /dispatch` request body — `DispatchRequest` |
| `events.schema.json`  | `GET /stream` SSE data line — `BusEvent` envelope |

## What these files ARE — and aren't

**`tools.schema.json` is a shape contract, not a snapshot.** It describes
the shape of one `ToolSpec` plus the array envelope. Adding a new tool to
`ToolLayer.tool_specs()` does NOT require regenerating this file — only
changes to the `ToolSpec` model itself (new required field, renamed field)
do. The list of *which* tools the gateway currently advertises is fetched
at runtime from `GET /tools`; it never lives in this file.

**`events.schema.json` is a projection, not a duplicate.** The wire shape
that flows over `GET /stream` is exactly the JSON dump of
`contracts.schema.Event` (the generic envelope: `type`, `source`,
`payload`, `ts_event`, `ts_ingest`, `event_id`, `seq`). This file is
generated from `Event[dict[str, Any]]` (aliased as `BusEvent` in
`contracts/gateway.py`) — there is only one definition. `ts_ingest` and
`event_id` have server-side defaults, so they appear in JSON Schema's
`optional` group, but the server always fills them in on the wire.

The TypeScript client in `packages/tool-client/` may consume these files
or hand-write equivalent types — both are valid as long as the runtime
behavior matches.
