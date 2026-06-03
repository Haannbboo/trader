/**
 * JSON Schema -> TypeBox runtime converter.
 *
 * The gateway's `GET /tools` returns tool specs whose `parameters` is a
 * JSON Schema object. Pi Agent's `AgentTool.parameters` requires a TypeBox
 * `TSchema`. This module bridges the two at runtime — no codegen step.
 *
 * Scope: the gateway's actual surface today is small and fixed (string
 * primitives, enums, arrays of strings, nested objects, optional fields).
 * Anything we haven't seen yet falls back to `Type.Unknown()` so the
 * tool still loads, even if the typed args the LLM gets back are looser.
 * Extend the matrix when a new schema shape ships in the gateway.
 */

import { type TSchema, Type } from "@sinclair/typebox";

/** Subset of JSON Schema we know how to convert. The full type is much larger;
 *  this is the practical surface we accept from the gateway. Extra fields are
 *  allowed (and ignored) so a producer that adds a new keyword (e.g. `oneOf`,
 *  `$ref`) doesn't fail the converter outright — it falls back to
 *  `Type.Unknown()` for the unknown shape, which keeps the tool loadable. */
export type JsonSchemaObject = {
  type?: string;
  properties?: Record<string, JsonSchemaObject>;
  required?: string[];
  additionalProperties?: boolean;
  items?: JsonSchemaObject;
  enum?: ReadonlyArray<string | number | boolean>;
  description?: string;
  default?: unknown;
  // Index signature lets the converter receive unrecognised shapes (e.g.
  // `oneOf`, `anyOf`, `$ref`) without TS rejecting the call. The converter
  // ignores anything outside the named fields above.
  [key: string]: unknown;
};

/**
 * Convert a JSON Schema object into a TypeBox `TSchema`.
 * Unrecognized shapes (e.g. a `true` boolean schema, `oneOf`, `$ref`) fall
 * back to `Type.Unknown()` so the tool stays loadable; the gateway never
 * produces those today, and silently loading them keeps the LLM working even
 * if the gateway's surface grows ahead of this matrix.
 */
export function jsonSchemaToTypeBox(schema: JsonSchemaObject): TSchema {
  if (schema.type === "object" || schema.properties !== undefined) {
    return convertObject(schema);
  }
  if (schema.type === "array") {
    return Type.Array(schema.items ? jsonSchemaToTypeBox(schema.items) : Type.Unknown());
  }
  if (schema.type === "string") {
    if (schema.enum && schema.enum.length > 0) {
      return Type.Union(
        schema.enum.map((v) => Type.Literal(v as string | number | boolean)),
        { description: schema.description },
      );
    }
    return Type.String({ description: schema.description });
  }
  if (schema.type === "number" || schema.type === "integer") {
    return Type.Number({ description: schema.description });
  }
  if (schema.type === "boolean") {
    return Type.Boolean({ description: schema.description });
  }
  // Unknown shape (null, union, $ref, ...). Fall back to Unknown so the tool
  // still loads; surface a typed value at the call site if precision matters.
  return Type.Unknown();
}

function convertObject(schema: JsonSchemaObject): TSchema {
  const props: Record<string, TSchema> = {};
  const required = new Set(schema.required ?? []);
  for (const [key, propSchema] of Object.entries(schema.properties ?? {})) {
    const converted = jsonSchemaToTypeBox(propSchema);
    props[key] = required.has(key) ? converted : Type.Optional(converted);
  }
  return Type.Object(props, {
    additionalProperties: schema.additionalProperties ?? false,
  });
}
