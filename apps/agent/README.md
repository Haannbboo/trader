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

# default: TUI (interactive). Opens a session in ~/.pi/agent/sessions/.
just agent

# headless: one-shot, prints the response and exits
just agent -p "What's my cash balance?"

# TUI, continue the most recent session
just agent -c

# TUI, pick from a list of past sessions
just agent -r

# explicit provider
just agent --provider openai -p "Show me my positions"
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

## TUI mode

`just agent` (no `-p`) launches the interactive TUI. The TUI:

- Loads the system prompt from `apps/agent/system-prompt.md` (or
  `AGENT_SYSTEM_PROMPT_FILE`).
- Exposes the gateway tool catalog as `customTools`. Built-in tools
  (read, bash, edit, write, grep, find, ls) are off by default.
- Persists sessions to `~/.pi/agent/sessions/<repo-encoded-cwd>/`.
  Sessions are scoped to the repo, not the `apps/agent` subdirectory.
- Supports the framework's slash commands: `/new`, `/resume`, `/fork`,
  model switching via Ctrl+P, etc.

The system-prompt file and the four optional features are toggled via
env vars in the repo-root `.env`:

| Var | Default | Effect |
|---|---|---|
| `AGENT_SYSTEM_PROMPT_FILE` | `<repo>/apps/agent/system-prompt.md` | Path to the system-prompt markdown. Required. |
| `AGENT_ENABLE_SKILLS` | `false` | When `true`, the SDK discovers skills. |
| `AGENT_ENABLE_CONTEXT_FILES` | `false` | When `true`, AGENTS.md / CLAUDE.md are loaded. |
| `AGENT_ENABLE_EXTENSIONS` | `false` | When `true`, project-level extensions are loaded. |
| `AGENT_ENABLE_BUILTIN_TOOLS` | `false` | When `true`, the agent gets read/bash/edit/write/grep/find/ls. |

## Status

v2 = interactive TUI (default) + one-shot headless (`-p`) + persistent
sessions (`-c` / `-r`). Gateway tool surface is unchanged from v1.
