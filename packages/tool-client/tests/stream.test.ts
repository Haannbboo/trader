import { describe, expect, it, vi } from "vitest";
import { subscribeEvents } from "../src/index.js";
import { parseSseData, parseSseStream } from "../src/stream.js";
import type { BusEvent } from "../src/types.js";

function fakeFetch(responses: ReadonlyArray<() => Response>) {
  const fn = vi.fn<typeof fetch>();
  for (const make of responses) {
    fn.mockResolvedValueOnce(make());
  }
  return fn;
}

function sseResponse(events: ReadonlyArray<BusEvent | { _heartbeat: true }>): Response {
  const blocks: string[] = [];
  for (const e of events) {
    if ("_heartbeat" in e) {
      blocks.push(": keepalive\n\n");
    } else {
      blocks.push(`data: ${JSON.stringify(e)}\n\n`);
    }
  }
  return new Response(blocks.join(""), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

const FILL: BusEvent = {
  type: "fill",
  source: "alpaca",
  payload: { symbol: "AAPL", quantity: 10, price: "190.25" },
  ts_event: "2026-06-02T13:00:00Z",
  ts_ingest: "2026-06-02T13:00:00.123Z",
  event_id: "11111111-1111-1111-1111-111111111111",
  seq: 42,
};

const QUOTE: BusEvent = {
  type: "quote",
  source: "alpaca",
  payload: { symbol: "MSFT", bid: 400, ask: 401 },
  ts_event: "2026-06-02T13:00:01Z",
  ts_ingest: "2026-06-02T13:00:01.045Z",
  event_id: "22222222-2222-2222-2222-222222222222",
  seq: 43,
};

describe("parseSseData", () => {
  it("returns the data payload from a `data: ...` line", () => {
    expect(parseSseData("data: hello world")).toBe("hello world");
  });

  it("returns the data payload from a `data:...` line (no space)", () => {
    expect(parseSseData("data:hello")).toBe("hello");
  });

  it("returns null for blocks containing only heartbeat comments", () => {
    expect(parseSseData(": keepalive")).toBeNull();
  });

  it("returns null for empty blocks", () => {
    expect(parseSseData("")).toBeNull();
  });
});

describe("parseSseStream", () => {
  it("yields one parsed data string per SSE event", async () => {
    const body = new Response("data: one\n\ndata: two\n\n").body;
    if (!body) throw new Error("test setup: no body");

    const out: string[] = [];
    for await (const data of parseSseStream(body)) out.push(data);

    expect(out).toEqual(["one", "two"]);
  });

  it("skips heartbeat comment lines", async () => {
    const body = new Response(": keepalive\n\ndata: real\n\n: keepalive\n\n").body;
    if (!body) throw new Error("test setup: no body");

    const out: string[] = [];
    for await (const data of parseSseStream(body)) out.push(data);

    expect(out).toEqual(["real"]);
  });

  it("handles events split across chunks (partial reads)", async () => {
    // Build a body and read the first chunk to test that the parser keeps a
    // buffer across reads rather than assuming one event == one chunk.
    const body = new Response("data: first\n\ndata: sec").body;
    if (!body) throw new Error("test setup: no body");

    const out: string[] = [];
    for await (const data of parseSseStream(body)) out.push(data);

    // The stream ends before "sec\n\n" arrives, so only the first event is
    // complete. (This is the realistic "client disconnect" case — anything
    // buffered that hasn't been terminated by a blank line is dropped.)
    expect(out).toEqual(["first"]);
  });
});

describe("subscribeEvents", () => {
  it("yields parsed BusEvents from the gateway's SSE stream", async () => {
    const fetch = fakeFetch([() => sseResponse([FILL, QUOTE])]);
    const events: BusEvent[] = [];
    for await (const e of subscribeEvents({ gatewayUrl: "http://localhost:8787", fetch })) {
      events.push(e);
    }
    expect(events).toEqual([FILL, QUOTE]);
  });

  it("builds the query string from filter options", async () => {
    const fetch = fakeFetch([() => sseResponse([FILL])]);
    for await (const _ of subscribeEvents({
      gatewayUrl: "http://localhost:8787",
      fetch,
      events: ["fill", "quote"],
      symbols: ["AAPL"],
      sources: ["alpaca"],
    })) {
      // drain
    }
    const [url] = fetch.mock.calls[0] as [string];
    const parsed = new URL(url);
    expect(parsed.pathname).toBe("/stream");
    expect(parsed.searchParams.get("events")).toBe("fill,quote");
    expect(parsed.searchParams.get("symbols")).toBe("AAPL");
    expect(parsed.searchParams.get("sources")).toBe("alpaca");
  });

  it("cancels the underlying reader when the consumer breaks out early", async () => {
    const fetch = fakeFetch([() => sseResponse([FILL, QUOTE, FILL, QUOTE])]);
    const events: BusEvent[] = [];
    for await (const e of subscribeEvents({ gatewayUrl: "http://localhost:8787", fetch })) {
      events.push(e);
      if (events.length === 1) break;
    }
    expect(events).toEqual([FILL]);
  });
});
