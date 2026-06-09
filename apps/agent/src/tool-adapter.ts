/**
 * tool-adapter — AgentTool (from @earendil-works/pi-agent-core) → ToolDefinition
 * (from @earendil-works/pi-coding-agent).
 *
 * Why this exists: the forwarder in `apps/agent/src/forwarder` is built on
 * `pi-agent-core` and returns `AgentTool[]` with a 4-arg `execute(toolCallId,
 * params, signal?, onUpdate?)`. The `pi-coding-agent` SDK's `customTools` slot
 * expects `ToolDefinition[]` with a 5-arg `execute(toolCallId, params, signal,
 * onUpdate, ctx)`. The signatures are mostly compatible — both return
 * `AgentToolResult<TDetails>` — but the SDK requires the trailing `ctx` and
 * the type system can't unify them.
 *
 * This module is the only place the two type systems touch. The wrapper
 * drops `signal`, `onUpdate`, and `ctx` (the forwarder doesn't use them)
 * and forwards the first two args straight through.
 */

import type { AgentTool } from "@earendil-works/pi-agent-core";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";

export function wrapAgentTool(agentTool: AgentTool<any>): ToolDefinition {
  return defineTool({
    name: agentTool.name,
    label: agentTool.label,
    description: agentTool.description,
    parameters: agentTool.parameters,
    execute: async (toolCallId, params) =>
      agentTool.execute(toolCallId, params as never, undefined, undefined),
  });
}
