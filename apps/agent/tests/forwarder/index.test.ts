import { describe, expect, it } from "vitest";
import { VERSION } from "../../src/forwarder/index.js";

describe("tool-client skeleton", () => {
  it("exports a version constant", () => {
    expect(VERSION).toBe("0.1.0");
  });
});
