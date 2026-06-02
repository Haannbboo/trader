# tools-client

Thin TypeScript forwarder for the [AgentGateway](../../apps/live/pi_gateway.py)
HTTP seam. Given a gateway URL, this package produces an `AgentTool[]` derived
from `GET /tools`; each tool's `execute` body forwards to `POST /dispatch`.

The package owns no reasoning loop and no LLM calls. The consumer is an
external Pi Agent process (typically `@earendil-works/pi-agent-core`); this
package just hands it the tool surface.

## Status

Skeleton on `feat/ts-tools-client`. Implementation lands in subsequent
commits: HTTP client, JSON-Schema → TypeBox bridge, tools adapter, stream
adapter, end-to-end test.

## Layout

- `src/` — source
- `tests/` — vitest tests
- `dist/` — build output (gitignored)

## Commands

```sh
pnpm install
pnpm test
pnpm typecheck
pnpm lint
```
