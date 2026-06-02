/**
 * tools-client — a thin TypeScript forwarder for the AgentGateway HTTP seam.
 *
 * Given a gateway URL, this package produces an `AgentTool[]` derived from
 * the gateway's `GET /tools` response. Each tool's `execute` body forwards
 * to `POST /dispatch`. The package owns no reasoning loop and no LLM calls;
 * the consumer (an external Pi Agent process) drives the loop, and this
 * package just hands it the tool surface.
 *
 * This file is the public entry point. Step 2 ships the skeleton; steps 3-6
 * land the actual HTTP client, schema bridge, tools adapter, and stream
 * adapter. Re-exports are added here as each module lands.
 *
 * @packageDocumentation
 */

/** Package version, kept in lockstep with `package.json`. */
export const VERSION = "0.1.0" as const;
