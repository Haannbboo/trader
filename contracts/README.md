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

The TypeScript client in `packages/tools-client/` imports these files
directly so both sides of the seam agree on every shape.
