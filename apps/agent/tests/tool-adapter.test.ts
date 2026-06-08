import type { AgentTool, AgentToolResult } from "@earendil-works/pi-agent-core";
import { describe, expect, it, vi } from "vitest";
import { wrapAgentTool } from "../src/tool-adapter.js";

function makeStubAgentTool(): AgentTool<any> & { execute: ReturnType<typeof vi.fn> } {
  const execute = vi.fn(async (_id: string, params: unknown) => ({
    content: [{ type: "text" as const, text: `got ${JSON.stringify(params)}` }],
    details: { echoed: params },
  }));
  return {
    name: "balance",
    label: "Balance",
    description: "Returns the cash balance",
    parameters: { type: "object", properties: { account: { type: "string" } } } as never,
    execute,
  };
}

describe("wrapAgentTool", () => {
  it("passes name, label, description, parameters through unchanged", () => {
    const inner = makeStubAgentTool();
    const wrapped = wrapAgentTool(inner);
    expect(wrapped.name).toBe("balance");
    expect(wrapped.label).toBe("Balance");
    expect(wrapped.description).toBe("Returns the cash balance");
    expect(wrapped.parameters).toEqual({
      type: "object",
      properties: { account: { type: "string" } },
    });
  });

  it("forwards the first two execute args (toolCallId, params) to the inner tool", async () => {
    const inner = makeStubAgentTool();
    const wrapped = wrapAgentTool(inner);
    await wrapped.execute("call-123", { account: "main" }, undefined, undefined, undefined as never);
    expect(inner.execute).toHaveBeenCalledTimes(1);
    expect(inner.execute).toHaveBeenCalledWith("call-123", { account: "main" }, undefined, undefined);
  });

  it("forwards the inner tool's return value unchanged", async () => {
    const inner = makeStubAgentTool();
    const wrapped = wrapAgentTool(inner);
    const result = (await wrapped.execute("id", { x: 1 }, undefined, undefined, undefined as never)) as AgentToolResult<unknown>;
    expect(result.content).toEqual([{ type: "text", text: 'got {"x":1}' }]);
    expect(result.details).toEqual({ echoed: { x: 1 } });
  });
});
