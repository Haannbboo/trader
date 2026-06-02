/**
 * GatewayClient — thin HTTP client for the three AgentGateway endpoints:
 *   GET  /tools     -> listTools()
 *   POST /dispatch  -> dispatch(name, args)
 *   GET  /stream    -> subscribe(opts) -> AsyncIterable<BusEvent>
 *
 * Error mapping (mirrors the gateway's policy at the seam):
 *   400 {error:"risk_rejected", ...}  -> RiskRejectedError
 *   400 {error:"bad_request", ...}    -> BadRequestError
 *   other non-2xx                     -> Error (raw status)
 *
 * Fetch is injectable for tests. The default is globalThis.fetch (Node 20+
 * undici-builtin, or any fetch-compatible polyfill the consumer has wired up).
 */

import { BadRequestError, RiskRejectedError } from "./errors.js";
import type { BusEvent, SubscribeOptions, ToolSpec } from "./types.js";

export interface ClientOptions {
  /** Base URL of the gateway, e.g. "http://127.0.0.1:8787". Trailing slashes trimmed. */
  readonly gatewayUrl: string;
  /** Extra request headers (Content-Type is set automatically for JSON bodies). */
  readonly headers?: Readonly<Record<string, string>>;
  /** Injectable fetch — defaults to globalThis.fetch. Tests use this seam. */
  readonly fetch?: typeof fetch;
}

export class GatewayClient {
  private readonly baseUrl: string;
  private readonly headers: Readonly<Record<string, string>>;
  private readonly doFetch: typeof fetch;

  constructor(opts: ClientOptions) {
    this.baseUrl = opts.gatewayUrl.replace(/\/+$/, "");
    this.headers = { "Content-Type": "application/json", ...(opts.headers ?? {}) };
    this.doFetch = opts.fetch ?? globalThis.fetch.bind(globalThis);
  }

  /** GET /tools — the agent-facing tool catalog. */
  async listTools(): Promise<ToolSpec[]> {
    const res = await this.doFetch(`${this.baseUrl}/tools`, {
      method: "GET",
      headers: this.headers,
    });
    if (!res.ok) {
      throw new Error(`listTools: HTTP ${res.status}`);
    }
    return (await res.json()) as ToolSpec[];
  }

  /** POST /dispatch {name, args} — run one tool call, return the JSON result. */
  async dispatch(name: string, args: Readonly<Record<string, unknown>>): Promise<unknown> {
    const res = await this.doFetch(`${this.baseUrl}/dispatch`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify({ name, args }),
    });
    if (res.status === 400) {
      const body = (await res.json()) as {
        detail: { error: string; reason: string; rule?: string };
      };
      const detail = body?.detail;
      if (detail?.error === "risk_rejected") {
        throw new RiskRejectedError(detail.reason, detail.rule ?? "unknown");
      }
      if (detail?.error === "bad_request") {
        throw new BadRequestError(detail.reason);
      }
    }
    if (!res.ok) {
      throw new Error(`dispatch(${name}): HTTP ${res.status}`);
    }
    return await res.json();
  }

  /** GET /stream — async iterator of bus events as SSE `data:` lines.
   *
   *  Heartbeat comment lines (`: keepalive`) are skipped. The connection is
   *  cancelled when the consumer breaks out of the loop (the `finally` block
   *  cancels the underlying reader), which propagates to the server's
   *  generator cleanup. */
  async *subscribe(opts: SubscribeOptions = {}): AsyncIterableIterator<BusEvent> {
    const params = new URLSearchParams();
    if (opts.events?.length) params.set("events", opts.events.join(","));
    if (opts.symbols?.length) params.set("symbols", opts.symbols.join(","));
    if (opts.sources?.length) params.set("sources", opts.sources.join(","));
    const qs = params.toString();
    const url = `${this.baseUrl}/stream${qs ? `?${qs}` : ""}`;

    const res = await this.doFetch(url, {
      method: "GET",
      headers: { ...this.headers, Accept: "text/event-stream" },
    });
    if (!res.ok) {
      throw new Error(`subscribe: HTTP ${res.status}`);
    }
    if (!res.body) {
      throw new Error("subscribe: response has no body");
    }

    const reader = res.body.getReader();
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
          const event = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + 2);
          const data = parseSseData(event);
          if (data !== null) {
            yield JSON.parse(data) as BusEvent;
          }
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
}

/** Extract the first `data:` line from one SSE event block, or null if the
 *  block contains only comments (heartbeats). Per the SSE spec, `data: ` and
 *  `data:` are both valid; we accept both forms. */
function parseSseData(rawBlock: string): string | null {
  for (const line of rawBlock.split("\n")) {
    if (line.startsWith("data: ")) return line.slice("data: ".length);
    if (line.startsWith("data:")) return line.slice("data:".length);
  }
  return null;
}
