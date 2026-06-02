import { describe, expect, it, vi } from "vitest";
import { BadRequestError, GatewayClient, RiskRejectedError } from "../../src/forwarder/index.js";
import type { BusEvent } from "../../src/forwarder/types.js";

/**
 * Build a fetch mock from a list of pre-canned responses, consumed in order.
 * Returned value is a `vi.fn()` mock — call `.mock.calls[i]` to assert on
 * URL/init without an extra cast.
 */
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

/** A minimal SSE body wrapping one or more `data:` lines. Heartbeats are `: keepalive`. */
function sseResponse(events: ReadonlyArray<BusEvent | { _heartbeat: true }>): Response {
  const blocks: string[] = [];
  for (const e of events) {
    if ("_heartbeat" in e) {
      blocks.push(": keepalive\n\n");
    } else {
      blocks.push(`data: ${JSON.stringify(e)}\n\n`);
    }
  }
  const body = blocks.join("");
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

const TOOL_SPEC = {
  name: "get_balance",
  description: "Fetch the cash/equity/buying-power balance for the account.",
  parameters: { type: "object", properties: {}, required: [] },
};

const BUS_EVENT: BusEvent = {
  type: "fill",
  source: "alpaca",
  payload: { symbol: "AAPL", quantity: 10, price: "190.25" },
  ts_event: "2026-06-02T13:00:00Z",
  ts_ingest: "2026-06-02T13:00:00.123Z",
  event_id: "11111111-1111-1111-1111-111111111111",
  seq: 42,
};

describe("GatewayClient.listTools", () => {
  it("returns the parsed catalog on 200", async () => {
    const fetch = fakeFetch([() => jsonResponse(200, [TOOL_SPEC])]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    const tools = await client.listTools();

    expect(tools).toEqual([TOOL_SPEC]);
    expect(fetch).toHaveBeenCalledTimes(1);
    const [calledUrl, calledInit] = fetch.mock.calls[0] as [string, RequestInit];
    expect(calledUrl).toBe("http://localhost:8787/tools");
    expect(calledInit.method).toBe("GET");
  });

  it("strips trailing slashes from the gateway URL", async () => {
    const fetch = fakeFetch([() => jsonResponse(200, [])]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787////", fetch });
    await client.listTools();
    const [url] = fetch.mock.calls[0] as [string];
    expect(url).toBe("http://localhost:8787/tools");
  });

  it("throws on non-2xx", async () => {
    const fetch = fakeFetch([() => new Response("nope", { status: 500 })]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });
    await expect(client.listTools()).rejects.toThrow(/HTTP 500/);
  });
});

describe("GatewayClient.dispatch", () => {
  it("POSTs the body and returns the JSON result on 200", async () => {
    const result = { cash: "1000.00", equity: "1500.00", buying_power: "2000.00" };
    const fetch = fakeFetch([() => jsonResponse(200, result)]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    const out = await client.dispatch("get_balance", {});

    expect(out).toEqual(result);
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8787/dispatch");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ name: "get_balance", args: {} });
  });

  it("throws RiskRejectedError on 400 with error=risk_rejected", async () => {
    const fetch = fakeFetch([
      () =>
        jsonResponse(400, {
          detail: { error: "risk_rejected", reason: "max position exceeded", rule: "max_position" },
        }),
    ]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    let caught: unknown;
    try {
      await client.dispatch("place_order", { symbol: "AAPL", quantity: "999" });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(RiskRejectedError);
    const err = caught as RiskRejectedError;
    expect(err.reason).toBe("max position exceeded");
    expect(err.rule).toBe("max_position");
  });

  it("throws BadRequestError on 400 with error=bad_request", async () => {
    const fetch = fakeFetch([
      () => jsonResponse(400, { detail: { error: "bad_request", reason: "unknown tool: foo" } }),
    ]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    await expect(client.dispatch("foo", {})).rejects.toBeInstanceOf(BadRequestError);
  });

  it("throws generic Error on 5xx", async () => {
    const fetch = fakeFetch([() => new Response("boom", { status: 500 })]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });
    await expect(client.dispatch("get_balance", {})).rejects.toThrow(/HTTP 500/);
  });
});

describe("GatewayClient.subscribe", () => {
  it("yields parsed events from SSE data lines", async () => {
    const fetch = fakeFetch([() => sseResponse([BUS_EVENT])]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    const events: BusEvent[] = [];
    for await (const e of client.subscribe()) {
      events.push(e);
    }
    expect(events).toEqual([BUS_EVENT]);
  });

  it("skips heartbeat comment lines", async () => {
    const fetch = fakeFetch([() => sseResponse([{ _heartbeat: true }, BUS_EVENT])]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    const events: BusEvent[] = [];
    for await (const e of client.subscribe()) events.push(e);
    expect(events).toEqual([BUS_EVENT]);
  });

  it("builds the query string from filter options", async () => {
    const fetch = fakeFetch([() => sseResponse([BUS_EVENT])]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    for await (const _ of client.subscribe({
      events: ["fill", "quote"],
      symbols: ["AAPL", "MSFT"],
      sources: ["alpaca"],
    })) {
      // drain
    }

    const [url] = fetch.mock.calls[0] as [string];
    const parsed = new URL(url);
    expect(parsed.pathname).toBe("/stream");
    expect(parsed.searchParams.get("events")).toBe("fill,quote");
    expect(parsed.searchParams.get("symbols")).toBe("AAPL,MSFT");
    expect(parsed.searchParams.get("sources")).toBe("alpaca");
  });

  it("omits empty filter params from the query string", async () => {
    const fetch = fakeFetch([() => sseResponse([BUS_EVENT])]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    for await (const _ of client.subscribe({})) {
      // drain
    }

    const [url] = fetch.mock.calls[0] as [string];
    expect(url).toBe("http://localhost:8787/stream");
  });

  it("throws on non-2xx", async () => {
    const fetch = fakeFetch([() => new Response("nope", { status: 502 })]);
    const client = new GatewayClient({ gatewayUrl: "http://localhost:8787", fetch });

    const it1 = client.subscribe();
    await expect(it1.next()).rejects.toThrow(/HTTP 502/);
  });
});
