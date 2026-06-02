# apps/agent

One-shot Pi Agent driver. Reads a prompt, fetches tools from the
[AgentGateway](../../apps/live/pi_gateway.py) over HTTP via the local
`src/forwarder/`, constructs an `Agent` from `@earendil-works/pi-agent-core`,
and prints the streamed result.

The package owns two things:

- `src/forwarder/` — the HTTP forwarder (the "tool client" renamed).
  Private to this app; not a separate library.
- `src/main.ts` — the ~30-line runner.

## Run

Prereqs: `apps/live` running on `:8787` (or whatever `GATEWAY_URL` points
at), and an LLM API key for the provider you pick.

```sh
# one-time
pnpm install

# drive a one-shot prompt
just agent "What's my cash balance?"
# equivalent: cd apps/agent && pnpm start -- "What's my cash balance?"

# or via env vars / different provider
GATEWAY_URL=http://localhost:8787 \
LLM_PROVIDER=openai LLM_MODEL=gpt-4o \
just agent "Show me my positions and the latest fill events"
```

## Env

| Var | Default | Notes |
|---|---|---|
| `GATEWAY_URL` | `http://127.0.0.1:8787` | Where the AgentGateway is serving |
| `LLM_PROVIDER` | `anthropic` | Provider name for `getModel(...)` |
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Model name for the provider |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | Required; pi-ai reads from env |

## Status

v1 = one-shot only. No REPL, no `/stream` event injection, no session
persistence. Each `just agent` invocation is a fresh `Agent` instance.

When those land, they'll be configuration of the existing framework
hooks (`beforeToolCall`, `afterToolCall`, `transformContext`), not new
infrastructure.
