/**
 * tools-client — a thin TypeScript forwarder for the AgentGateway HTTP seam.
 *
 * Given a gateway URL, this package produces an `AgentTool[]` derived from
 * the gateway's `GET /tools` response. Each tool's `execute` body forwards
 * to `POST /dispatch`. The package owns no reasoning loop and no LLM calls;
 * the consumer (an external Pi Agent process) drives the loop, and this
 * package just hands it the tool surface.
 *
 * @packageDocumentation
 */

export { GatewayClient, type ClientOptions } from "./client.js";
export { BadRequestError, RiskRejectedError } from "./errors.js";
export {
  type SubscribeEventsOptions,
  parseSseData,
  parseSseStream,
  subscribeEvents,
} from "./stream.js";
export { type CreateToolsOptions, createTools } from "./tools.js";
export type { BusEvent, EventType, SubscribeOptions, ToolSpec } from "./types.js";

/** Package version, kept in lockstep with `package.json`. */
export const VERSION = "0.1.0" as const;
