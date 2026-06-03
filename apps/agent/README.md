# apps/agent

One-shot Pi Agent driver. Reads a prompt, fetches tools from the
[AgentGateway](../../apps/live/pi_gateway.py) over HTTP via the local
`src/forwarder/`, constructs an `Agent` from `@earendil-works/pi-agent-core`,
and prints the streamed result.

The package owns two things:

- `src/forwarder/` — the HTTP forwarder (the "tool client" renamed).
  Private to this app; not a separate library.
- `src/main.ts` — the runner.
- `src/config.ts` — the CLI + env config resolver (pure, unit-tested).

## Run

Prereqs: `apps/live` running on `:8787` (or whatever `GATEWAY_URL` points
at), and an LLM API key + model set in the repo `.env` (or your shell).

```sh
# one-time
pnpm install

# default: anthropic
just agent "What's my cash balance?"

# explicit provider via the --provider flag
just agent --provider openai "Show me my positions"
just agent --provider google "Summarize today's fills"
```

## Env

The runner reads a Claude Code / Codex-style triple of env vars per
provider. The flag selects which triple.

| Var | Required | Notes |
|---|---|---|
| `GATEWAY_URL` | no | Where the AgentGateway is serving. Default: `http://127.0.0.1:8787` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | yes (for that provider) | pi-ai reads these natively. **For Google, use `GEMINI_API_KEY` — not `GOOGLE_API_KEY` — this is pi-ai's convention.** |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` / `GOOGLE_MODEL` | yes (for that provider) | Passed as the second arg to `getModel(provider, modelName)`. |
| `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` / `GOOGLE_BASE_URL` | no | Override the endpoint (e.g. for a local LLM proxy). Applied as `model.baseUrl` before the first request. |

All four are picked up from the repo-root `.env` automatically
(`apps/agent` loads it on startup; explicit shell env beats `.env`).

### Examples

```sh
# default Anthropic, base URL unset
just agent "What's my balance?"

# OpenAI with a specific model
OPENAI_MODEL=gpt-4o-mini just agent --provider openai "Quick check on positions"

# Anthropic-compatible proxy (e.g. a local LLM server speaking the
# Anthropic wire format)
ANTHROPIC_BASE_URL=http://localhost:8080 just agent "What's my balance?"
```

## Status

v1 = one-shot only. No REPL, no `/stream` event injection, no session
persistence. Each `just agent` invocation is a fresh `Agent` instance.

When those land, they'll be configuration of the existing framework
hooks (`beforeToolCall`, `afterToolCall`, `transformContext`), not new
infrastructure.
