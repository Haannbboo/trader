/**
 * stream — Server-Sent Events parser and a high-level `subscribeEvents`
 * convenience. The parser is a focused async generator: feed it a
 * `ReadableStream<Uint8Array>`, get back one parsed data string per event.
 * Heartbeat comment lines (those starting with `:`) are skipped.
 *
 * `subscribeEvents` is the one-call entry point for consumers that don't need
 * a `GatewayClient` instance for anything else. It builds a client, calls
 * `subscribe()`, and re-yields the parsed `BusEvent`s.
 */

import { GatewayClient } from "./client.js";
import type { BusEvent, SubscribeOptions } from "./types.js";

/** Extract the first `data:` line from one SSE event block, or null if the
 *  block contains only comments (heartbeats). Per the SSE spec, `data: ` and
 *  `data:` are both valid; we accept both forms. */
export function parseSseData(rawBlock: string): string | null {
  for (const line of rawBlock.split("\n")) {
    if (line.startsWith("data: ")) return line.slice("data: ".length);
    if (line.startsWith("data:")) return line.slice("data:".length);
  }
  return null;
}

/**
 * Parse an SSE body into one parsed data string per event. Heartbeat comments
 * (lines starting with `:`) and empty lines are skipped. The generator ends
 * when the stream signals done; callers that want to disconnect early should
 * `break` out of the loop, which lets the surrounding `try { } finally { }`
 * cancel the reader.
 */
export async function* parseSseStream(
  body: ReadableStream<Uint8Array>,
): AsyncIterableIterator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Drain complete SSE events (each terminated by a blank line).
      let sepIdx = buffer.indexOf("\n\n");
      while (sepIdx !== -1) {
        const block = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        const data = parseSseData(block);
        if (data !== null) yield data;
        sepIdx = buffer.indexOf("\n\n");
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // Stream already closed by the server; cancellation is a no-op.
    }
  }
}

export interface SubscribeEventsOptions {
  /** Base URL of the gateway, e.g. "http://127.0.0.1:8787". */
  readonly gatewayUrl: string;
  /** Filter dimensions; empty = match all. See `SubscribeOptions`. */
  readonly events?: SubscribeOptions["events"];
  readonly symbols?: SubscribeOptions["symbols"];
  readonly sources?: SubscribeOptions["sources"];
  /** Extra request headers passed through to the SSE call. */
  readonly headers?: Readonly<Record<string, string>>;
  /** Injectable fetch — defaults to globalThis.fetch. */
  readonly fetch?: typeof fetch;
}

/**
 * Open a streaming subscription to the gateway and yield typed `BusEvent`s.
 * One-call convenience for consumers that don't need a `GatewayClient`
 * instance for anything else (e.g. a small script that just tails fills).
 */
export async function* subscribeEvents(
  opts: SubscribeEventsOptions,
): AsyncIterableIterator<BusEvent> {
  const client = new GatewayClient({
    gatewayUrl: opts.gatewayUrl,
    headers: opts.headers,
    fetch: opts.fetch,
  });
  yield* client.subscribe({
    ...(opts.events !== undefined && { events: opts.events }),
    ...(opts.symbols !== undefined && { symbols: opts.symbols }),
    ...(opts.sources !== undefined && { sources: opts.sources }),
  });
}
