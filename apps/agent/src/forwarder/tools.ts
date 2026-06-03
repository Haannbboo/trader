/**
 * tools adapter — `createTools(opts)` fetches the gateway's tool catalog and
 * returns one `AgentTool` per spec. Each tool's `execute` body is a one-line
 * forward to `POST /dispatch`.
 *
 * This is the *only* place the TS client and the gateway touch. The consumer
 * (a Pi Agent process) gets a fully-formed `AgentTool[]` and never has to
 * know there's a network round-trip in the middle.
 */

import type { AgentTool } from "@earendil-works/pi-agent-core";
import type { TSchema } from "@sinclair/typebox";
import { GatewayClient } from "./client.js";
import { type JsonSchemaObject, jsonSchemaToTypeBox } from "./schemas.js";
import type { ToolSpec } from "./types.js";

export interface CreateToolsOptions {
  /** Base URL of the gateway, e.g. "http://127.0.0.1:8787". */
  readonly gatewayUrl: string;
  /** Extra request headers passed through to every call. */
  readonly headers?: Readonly<Record<string, string>>;
  /** Injectable fetch — defaults to globalThis.fetch. Tests use this seam. */
  readonly fetch?: typeof fetch;
}

/**
 * Fetch the gateway's `GET /tools` and return one `AgentTool` per spec.
 * Throws if the catalog request fails (e.g. gateway is down, auth missing).
 */
export async function createTools(opts: CreateToolsOptions): Promise<AgentTool<TSchema>[]> {
  const client = new GatewayClient(opts);
  const specs = await client.listTools();
  return specs.map((spec) => makeAgentTool(spec, client));
}

function makeAgentTool(spec: ToolSpec, client: GatewayClient): AgentTool<TSchema> {
  // The `parameters` field in a `ToolSpec` is a free-form JSON Schema object.
  // TypeBox can't read JSON Schema at compile time, so we convert at runtime.
  // The cast through `unknown` is safe: `jsonSchemaToTypeBox` only sees the
  // `JsonSchemaObject` shape we get from the gateway.
  const parameters = jsonSchemaToTypeBox(spec.parameters as unknown as JsonSchemaObject);
  return {
    name: spec.name,
    // Label is what the UI displays. The spec's name is already human-readable
    // for our surface (snake_case verbs); we can humanize it later if the
    // gateway starts emitting tool names that aren't.
    label: spec.name,
    description: spec.description,
    parameters,
    execute: async (_toolCallId, params) => {
      // `params` is typed as `Static<TParameters>` by the runtime — some
      // object shape derived from `parameters`. We hand it through to the
      // gateway verbatim; the gateway is the validator.
      const result = await client.dispatch(spec.name, params as Record<string, unknown>);
      const text = typeof result === "string" ? result : JSON.stringify(result);
      return {
        content: [{ type: "text", text }],
        details: result,
      };
    },
  };
}
