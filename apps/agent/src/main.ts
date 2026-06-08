/**
 * apps/agent — the Pi Agent driver (one-shot, v1).
 *
 * Reads a prompt from CLI args, fetches tools from the AgentGateway via the
 * local `forwarder`, constructs an `Agent` from `@earendil-works/pi-agent-core`,
 * registers the tools, and streams the result. Exits when the agent settles.
 *
 * Config resolution (CLI + env) lives in `./config.js`; this file is the
 * wiring layer.
 *
 * .env loading: walks up from this file to find the repo root (marker:
 * `pyproject.toml`), then loads `.env` from there if present. Explicit
 * shell env always wins over .env (dotenv default).
 */

import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Agent } from "@earendil-works/pi-agent-core";
import { getModel, getModels } from "@earendil-works/pi-ai";
import { config as dotenvConfig } from "dotenv";
import { ConfigError, type ResolvedConfig, resolveConfig } from "./config.js";
import { createTools } from "./forwarder/index.js";
import { findRepoRoot } from "./repo.js";

// --- 1. .env loading from the repo root ------------------------------------
const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(findRepoRoot(__dirname), ".env");
if (existsSync(envPath)) {
  // `override: false` (the default) means explicit shell env beats .env.
  dotenvConfig({ path: envPath });
}

// --- 2. Config resolution --------------------------------------------------
let resolved: ResolvedConfig;
try {
  resolved = resolveConfig(process.argv.slice(2), process.env);
} catch (e) {
  if (e instanceof ConfigError) {
    console.error(`[agent] ${e.message}`);
    process.exit(2);
  }
  throw e;
}

const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://127.0.0.1:8787";

// --- 3. Tool fetch + Agent construction ------------------------------------
const tools = await createTools({ gatewayUrl: GATEWAY_URL });
console.error(`[agent] loaded ${tools.length} tools from ${GATEWAY_URL}:`);
for (const t of tools) console.error(`  - ${t.name}: ${t.description}`);
console.error(`[agent] provider=${resolved.provider} model=${resolved.modelName}`);

// The env string is `string` but `getModel`'s first arg is a closed
// `KnownProvider` union. We trust the env value at runtime (pi-ai will
// throw on an unknown provider); the cast is a TS-only concession.
let model = getModel(resolved.provider as never, resolved.modelName);
if (!model) {
  // pi-ai's `getModel` returns `undefined` for unknown model names — no
  // error, no fallback. The whole point of supporting *_BASE_URL is to
  // point at a custom endpoint that may serve a fine-tune, a self-hosted
  // model, or a proxy. For those cases, clone any known model for this
  // provider as a template — same `api`/`provider`/`baseUrl`/`compat`
  // wiring — and override `id`/`name` to the user's chosen value.
  const templates = getModels(resolved.provider as never);
  const template = templates[0];
  if (!template) {
    console.error(`[agent] provider ${resolved.provider} has no registered models in pi-ai`);
    process.exit(2);
  }
  model = { ...template, id: resolved.modelName, name: resolved.modelName };
  console.error(`[agent] using custom model name; cloned template from ${template.id}`);
}
if (resolved.baseUrl) {
  // The `Model` object returned by getModel() is mutable; pi-ai's providers
  // (anthropic, openai, google) read `model.baseUrl` when building the
  // HTTP request, so this overrides the endpoint without re-wiring the SDK.
  model.baseUrl = resolved.baseUrl;
  console.error(`[agent] baseUrl override: ${resolved.baseUrl}`);
}

const agent = new Agent({
  initialState: {
    systemPrompt:
      "You are a trading agent. Use the available tools to answer questions about the account.",
    model,
    tools,
  },
});

agent.subscribe((event) => {
  switch (event.type) {
    case "tool_execution_start":
      console.error(`[tool] ${event.toolName}(${JSON.stringify(event.args)})`);
      break;
    case "message_update":
      if (event.assistantMessageEvent.type === "text_delta") {
        process.stdout.write(event.assistantMessageEvent.delta);
      }
      break;
  }
});

await agent.prompt(resolved.prompt);
process.stderr.write("\n");
