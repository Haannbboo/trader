import { Type } from "@sinclair/typebox";
import { describe, expect, it, vi } from "vitest";
import { createTools } from "../src/index.js";
import type { ToolSpec } from "../src/types.js";

/** Pull the first tool out of `createTools`'s return, asserting via the test
 *  runner that exactly one was produced. Replaces `tools[0]!` (which biome's
 *  `noNonNullAssertion` rule rejects) with an explicit guard. */
async function singleTool(opts: Parameters<typeof createTools>[0]) {
  const tools = await createTools(opts);
  expect(tools).toHaveLength(1);
  const tool = tools[0];
  if (!tool) throw new Error("createTools returned no tools");
  return tool;
}

/** A reusable fetch mock that consumes pre-canned responses in order. */
function fakeFetch(responses: ReadonlyArray<() => Response>) {
  const fn = vi.fn<typeof fetch>();
  for (const make of responses) {
    fn.mockResolvedValueOnce(make());
  }
  return fn;
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const GET_BALANCE_SPEC: ToolSpec = {
  name: "get_balance",
  description: "Fetch cash, equity, and buying power balances for the account.",
  parameters: { type: "object", properties: {}, required: [] },
};

const PLACE_ORDER_SPEC: ToolSpec = {
  name: "place_order",
  description: "Place a new order (buy/sell). Enforced through risk guardrails.",
  parameters: {
    type: "object",
    properties: {
      client_order_id: { type: "string" },
      symbol: { type: "string" },
      side: { type: "string", enum: ["buy", "sell"] },
      quantity: { type: "string" },
    },
    required: ["client_order_id", "symbol", "side", "quantity"],
  },
};

describe("createTools", () => {
  it("returns one AgentTool per spec from the gateway", async () => {
    const fetch = fakeFetch([() => jsonResponse(200, [GET_BALANCE_SPEC, PLACE_ORDER_SPEC])]);
    const tools = await createTools({ gatewayUrl: "http://localhost:8787", fetch });

    expect(tools).toHaveLength(2);
    expect(tools[0]?.name).toBe("get_balance");
    expect(tools[1]?.name).toBe("place_order");
  });

  it("uses the spec name as the label", async () => {
    const fetch = fakeFetch([() => jsonResponse(200, [GET_BALANCE_SPEC])]);
    const tool = await singleTool({ gatewayUrl: "http://localhost:8787", fetch });
    expect(tool.label).toBe("get_balance");
  });

  it("preserves the spec description verbatim", async () => {
    const fetch = fakeFetch([() => jsonResponse(200, [GET_BALANCE_SPEC])]);
    const tool = await singleTool({ gatewayUrl: "http://localhost:8787", fetch });
    expect(tool.description).toBe(GET_BALANCE_SPEC.description);
  });

  it("converts the JSON Schema parameters to TypeBox at runtime", async () => {
    const fetch = fakeFetch([() => jsonResponse(200, [PLACE_ORDER_SPEC])]);
    const tool = await singleTool({ gatewayUrl: "http://localhost:8787", fetch });
    expect(tool.parameters).toEqual(
      Type.Object(
        {
          client_order_id: Type.String(),
          symbol: Type.String(),
          side: Type.Union([Type.Literal("buy"), Type.Literal("sell")]),
          quantity: Type.String(),
        },
        { additionalProperties: false },
      ),
    );
  });

  it("forwards an empty-object tool call to /dispatch and returns the JSON", async () => {
    const fetch = fakeFetch([
      () => jsonResponse(200, [GET_BALANCE_SPEC]),
      () => jsonResponse(200, { cash: "1000.00", equity: "1500.00", buying_power: "2000.00" }),
    ]);
    const tool = await singleTool({ gatewayUrl: "http://localhost:8787", fetch });

    const result = await tool.execute("call-1", {});

    expect(result.content).toEqual([
      {
        type: "text",
        text: JSON.stringify({ cash: "1000.00", equity: "1500.00", buying_power: "2000.00" }),
      },
    ]);
    expect(result.details).toEqual({ cash: "1000.00", equity: "1500.00", buying_power: "2000.00" });

    // The dispatch call should have hit /dispatch with name=get_balance, args={}.
    const [url, init] = fetch.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("http://localhost:8787/dispatch");
    expect(JSON.parse(init.body as string)).toEqual({ name: "get_balance", args: {} });
  });

  it("forwards a populated tool call to /dispatch verbatim", async () => {
    const fetch = fakeFetch([
      () => jsonResponse(200, [PLACE_ORDER_SPEC]),
      () => jsonResponse(200, { status: "filled", client_order_id: "abc" }),
    ]);
    const tool = await singleTool({ gatewayUrl: "http://localhost:8787", fetch });

    await tool.execute("call-2", {
      client_order_id: "abc",
      symbol: "AAPL",
      side: "buy",
      quantity: "10",
    });

    const [, init] = fetch.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      name: "place_order",
      args: { client_order_id: "abc", symbol: "AAPL", side: "buy", quantity: "10" },
    });
  });

  it("propagates RiskRejectedError from the gateway as a thrown error", async () => {
    const fetch = fakeFetch([
      () => jsonResponse(200, [PLACE_ORDER_SPEC]),
      () =>
        jsonResponse(400, {
          detail: { error: "risk_rejected", reason: "max position", rule: "max_pos" },
        }),
    ]);
    const tool = await singleTool({ gatewayUrl: "http://localhost:8787", fetch });

    await expect(
      tool.execute("call-3", { client_order_id: "x", symbol: "AAPL", side: "buy", quantity: "1" }),
    ).rejects.toThrow(/Risk rejected/);
  });
});
