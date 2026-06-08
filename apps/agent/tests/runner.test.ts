import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ConfigError, loadSystemPrompt } from "../src/runner.js";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "agent-runner-"));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("loadSystemPrompt", () => {
  it("reads a file and returns its trimmed contents", async () => {
    const path = join(dir, "prompt.md");
    writeFileSync(path, "  You are a trading agent.  \n");
    expect(await loadSystemPrompt(path)).toBe("You are a trading agent.");
  });

  it("trims trailing newlines and whitespace", async () => {
    const path = join(dir, "prompt.md");
    writeFileSync(path, "hi\n\n\n");
    expect(await loadSystemPrompt(path)).toBe("hi");
  });

  it("throws ConfigError when the file does not exist", async () => {
    await expect(loadSystemPrompt(join(dir, "missing.md"))).rejects.toBeInstanceOf(
      ConfigError,
    );
  });

  it("error message includes the missing path and a hint", async () => {
    const path = join(dir, "missing.md");
    await expect(loadSystemPrompt(path)).rejects.toThrow(/system prompt file not found/);
    await expect(loadSystemPrompt(path)).rejects.toThrow(/AGENT_SYSTEM_PROMPT_FILE/);
  });
});
