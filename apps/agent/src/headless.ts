/**
 * headless — the one-shot Pi Agent driver.
 *
 * Reads a prompt from `config.prompt`, fetches tools from the
 * AgentGateway via the local forwarder, constructs an `Agent` from
 * `@earendil-works/pi-agent-core`, and streams the result. Exits when
 * the agent settles.
 *
 * The system prompt is loaded from `config.systemPromptPath` — same
 * file the TUI uses, no hardcoded strings.
 */

import { Agent } from "@earendil-works/pi-agent-core";
import { createTools } from "./forwarder/index.js";
import { buildModel, loadSystemPrompt } from "./runner.js";
import type { ResolvedConfig } from "./config.js";

const GATEWAY_URL_ENV = "GATEWAY_URL";
const DEFAULT_GATEWAY_URL = "http://127.0.0.1:8787";

export async function runHeadless(config: ResolvedConfig): Promise<void> {
	if (config.prompt === undefined) {
		throw new Error("runHeadless: config.prompt is required (caller bug)");
	}

	const systemPrompt = await loadSystemPrompt(config.systemPromptPath);
	const model = buildModel(config);
	const gatewayUrl = process.env[GATEWAY_URL_ENV] ?? DEFAULT_GATEWAY_URL;
	const tools = await createTools({ gatewayUrl });

	console.error(`[agent] loaded ${tools.length} tools from ${gatewayUrl}:`);
	for (const t of tools) console.error(`  - ${t.name}: ${t.description}`);
	console.error(`[agent] provider=${config.provider} model=${config.modelName}`);

	const agent = new Agent({
		initialState: {
			systemPrompt,
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

	await agent.prompt(config.prompt);
	process.stderr.write("\n");
}
