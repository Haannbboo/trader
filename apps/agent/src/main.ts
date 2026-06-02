/**
 * apps/agent — the Pi Agent driver (one-shot, v1).
 *
 * Reads a prompt from CLI args, fetches tools from the AgentGateway via the
 * local `forwarder`, constructs an `Agent` from `@earendil-works/pi-agent-core`,
 * registers the tools, and streams the result. Exits when the agent settles.
 *
 * The agent loop, tool dispatch, message formatting, and event streaming are
 * all handled by the framework — this file is glue. See ADR-0002 for the
 * contracts strategy that keeps the forwarder's hand-written types in sync
 * with the Python `ToolSpec` / `DispatchRequest` Pydantic models.
 */

import { Agent } from "@earendil-works/pi-agent-core";
import { getModel } from "@earendil-works/pi-ai";
import { createTools } from "./forwarder/index.js";

const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://127.0.0.1:8787";
const LLM_PROVIDER = process.env.LLM_PROVIDER ?? "anthropic";
const LLM_MODEL = process.env.LLM_MODEL ?? "claude-sonnet-4-20250514";

const prompt = process.argv.slice(2).join(" ").trim();
if (!prompt) {
  console.error("usage: agent <prompt>  (or set GATEWAY_URL/LLM_PROVIDER/LLM_MODEL in env)");
  process.exit(2);
}

const tools = await createTools({ gatewayUrl: GATEWAY_URL });
console.error(`[agent] loaded ${tools.length} tools from ${GATEWAY_URL}:`);
for (const t of tools) console.error(`  - ${t.name}: ${t.description}`);

const agent = new Agent({
  initialState: {
    systemPrompt:
      "You are a trading agent. Use the available tools to answer questions about the account.",
    // The env string is `string` but `getModel`'s first arg is a closed
    // `KnownProvider` union. We trust the env value at runtime (pi-ai will
    // throw on an unknown provider); the cast is a TS-only concession.
    model: getModel(LLM_PROVIDER as never, LLM_MODEL),
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

await agent.prompt(prompt);
process.stderr.write("\n");
