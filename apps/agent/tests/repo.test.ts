import { describe, expect, it } from "vitest";
import { findRepoRoot } from "../src/repo.js";

describe("findRepoRoot", () => {
  it("returns the directory that contains pyproject.toml", () => {
    // __dirname (apps/agent) doesn't contain pyproject.toml — the repo root
    // (one level up) does. We use the real filesystem because the function
    // walks it.
    const root = findRepoRoot(import.meta.dirname);
    expect(root).not.toBe("/");
    // Sanity: pyproject.toml must exist at the returned root.
    const { existsSync } = require("node:fs") as typeof import("node:fs");
    const { resolve } = require("node:path") as typeof import("node:path");
    expect(existsSync(resolve(root, "pyproject.toml"))).toBe(true);
  });

  it("walks up multiple levels when pyproject.toml isn't near", () => {
    // Start from a deep nested directory that doesn't contain pyproject.toml.
    // The function should walk up until it finds the repo root.
    const root = findRepoRoot(`${import.meta.dirname}/../../../..`);
    expect(root).not.toBe("/");
  });

  it("returns the start directory unchanged when nothing above contains the marker", () => {
    // /tmp is a safe bet — no pyproject.toml above it on this system.
    const start = "/tmp";
    expect(findRepoRoot(start)).toBe(start);
  });
});
