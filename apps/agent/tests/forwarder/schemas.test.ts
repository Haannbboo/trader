import { Type } from "@sinclair/typebox";
import { describe, expect, it } from "vitest";
import { jsonSchemaToTypeBox } from "../../src/forwarder/schemas.js";

describe("jsonSchemaToTypeBox", () => {
  it("converts an empty object", () => {
    const tb = jsonSchemaToTypeBox({ type: "object", properties: {}, required: [] });
    expect(tb).toEqual(Type.Object({}, { additionalProperties: false }));
  });

  it("converts a flat object of required strings", () => {
    const schema = {
      type: "object",
      properties: {
        symbol: { type: "string", description: "Ticker" },
        side: { type: "string", description: "buy or sell" },
      },
      required: ["symbol", "side"],
    };
    const tb = jsonSchemaToTypeBox(schema);
    expect(tb).toEqual(
      Type.Object(
        {
          symbol: Type.String({ description: "Ticker" }),
          side: Type.String({ description: "buy or sell" }),
        },
        { additionalProperties: false },
      ),
    );
  });

  it("marks non-required fields as Optional", () => {
    const schema = {
      type: "object",
      properties: {
        symbol: { type: "string" },
        asset_class: { type: "string", enum: ["equity", "option", "crypto"], default: "equity" },
      },
      required: ["symbol"],
    };
    const tb = jsonSchemaToTypeBox(schema);
    expect(tb).toEqual(
      Type.Object(
        {
          symbol: Type.String(),
          asset_class: Type.Optional(
            Type.Union([Type.Literal("equity"), Type.Literal("option"), Type.Literal("crypto")]),
          ),
        },
        { additionalProperties: false },
      ),
    );
  });

  it("converts a string enum to a Type.Union of literals", () => {
    const schema = {
      type: "object",
      properties: { tif: { type: "string", enum: ["day", "gtc", "ioc", "fok"], default: "day" } },
      required: [],
    };
    const tb = jsonSchemaToTypeBox(schema);
    expect(tb).toEqual(
      Type.Object(
        {
          tif: Type.Optional(
            Type.Union([
              Type.Literal("day"),
              Type.Literal("gtc"),
              Type.Literal("ioc"),
              Type.Literal("fok"),
            ]),
          ),
        },
        { additionalProperties: false },
      ),
    );
  });

  it("converts an array of primitives", () => {
    const schema = {
      type: "object",
      properties: {
        symbols: { type: "array", items: { type: "string" } },
      },
      required: ["symbols"],
    };
    const tb = jsonSchemaToTypeBox(schema);
    expect(tb).toEqual(
      Type.Object({ symbols: Type.Array(Type.String()) }, { additionalProperties: false }),
    );
  });

  it("converts the place_order spec end-to-end", () => {
    // This mirrors the actual `place_order` spec from ToolLayer.tool_specs().
    const schema = {
      type: "object",
      properties: {
        client_order_id: { type: "string", description: "Unique client order ID for idempotency." },
        symbol: { type: "string", description: "Symbol of the instrument (e.g. AAPL)." },
        asset_class: {
          type: "string",
          enum: ["equity", "option", "crypto"],
          default: "equity",
          description: "Asset class of the instrument.",
        },
        side: { type: "string", enum: ["buy", "sell"], description: "Order side (buy or sell)." },
        quantity: { type: "string", description: "Quantity to trade." },
        order_type: {
          type: "string",
          enum: ["market", "limit", "stop", "stop_limit"],
          default: "market",
          description: "Type of the order.",
        },
        limit_price: { type: "string", description: "Limit price for limit/stop-limit orders." },
        tif: {
          type: "string",
          enum: ["day", "gtc", "ioc", "fok"],
          default: "day",
          description: "Time in force.",
        },
      },
      required: ["client_order_id", "symbol", "side", "quantity"],
    };

    const tb = jsonSchemaToTypeBox(schema);
    expect(tb).toEqual(
      Type.Object(
        {
          client_order_id: Type.String({ description: "Unique client order ID for idempotency." }),
          symbol: Type.String({ description: "Symbol of the instrument (e.g. AAPL)." }),
          asset_class: Type.Optional(
            Type.Union([Type.Literal("equity"), Type.Literal("option"), Type.Literal("crypto")], {
              description: "Asset class of the instrument.",
            }),
          ),
          side: Type.Union([Type.Literal("buy"), Type.Literal("sell")], {
            description: "Order side (buy or sell).",
          }),
          quantity: Type.String({ description: "Quantity to trade." }),
          order_type: Type.Optional(
            Type.Union(
              [
                Type.Literal("market"),
                Type.Literal("limit"),
                Type.Literal("stop"),
                Type.Literal("stop_limit"),
              ],
              { description: "Type of the order." },
            ),
          ),
          limit_price: Type.Optional(
            Type.String({ description: "Limit price for limit/stop-limit orders." }),
          ),
          tif: Type.Optional(
            Type.Union(
              [Type.Literal("day"), Type.Literal("gtc"), Type.Literal("ioc"), Type.Literal("fok")],
              { description: "Time in force." },
            ),
          ),
        },
        { additionalProperties: false },
      ),
    );
  });

  it("falls back to Type.Unknown for unrecognized shapes", () => {
    // `oneOf` is something the gateway doesn't produce today; the converter
    // should degrade gracefully rather than throw.
    const schema = {
      type: "object",
      properties: { x: { oneOf: [{ type: "string" }] } },
      required: ["x"],
    };
    const tb = jsonSchemaToTypeBox(schema);
    expect(tb).toEqual(Type.Object({ x: Type.Unknown() }, { additionalProperties: false }));
  });
});
