/**
 * Wire types for the AgentGateway HTTP seam.
 *
 * These mirror the JSON Schemas in /contracts/*.schema.json. If the Pydantic
 * models in packages/contracts change, regenerate with `just gen-contracts`
 * and update these types. Drift is caught by tests/client.test.ts which
 * cross-checks the runtime response against these shapes.
 */

/** One tool the agent can invoke. Returned by `GET /tools`. */
export type ToolSpec = {
  readonly name: string;
  readonly description: string;
  /** Free-form JSON Schema object describing the tool's args. */
  readonly parameters: Readonly<Record<string, unknown>>;
};

/** All bus event types the gateway can stream. Mirrors `EventType` in Pydantic. */
export type EventType =
  | "quote"
  | "bar"
  | "news"
  | "order_update"
  | "fill"
  | "position_update"
  | "balance_update"
  | "feature";

/** Envelope that flows over `GET /stream` as one SSE `data:` line per occurrence. */
export type BusEvent = {
  readonly type: EventType;
  readonly source: string;
  /** Generic over payload in Python (`Event[Quote]`, `Event[Fill]`, ...). */
  readonly payload: Readonly<Record<string, unknown>>;
  /** ISO 8601 datetime — when the fact happened at the source. */
  readonly ts_event: string;
  /** ISO 8601 datetime — when we received/normalized it. */
  readonly ts_ingest: string;
  /** UUID. */
  readonly event_id: string;
  /** Per-source monotonic seq for ordering/replay. */
  readonly seq: number | null;
};

/** Filter dimensions for `GET /stream`. Empty = match all, mirroring Subscription's
 * empty-tuple semantics on the Python side. */
export type SubscribeOptions = {
  readonly events?: readonly EventType[];
  readonly symbols?: readonly string[];
  readonly sources?: readonly string[];
};
