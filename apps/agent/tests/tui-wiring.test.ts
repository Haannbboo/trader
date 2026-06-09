import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";
import { buildSessionOptions } from "../src/tui.js";
import type { ResolvedConfig } from "../src/config.js";

function makeConfig(overrides: Partial<ResolvedConfig> = {}): ResolvedConfig {
  return {
    mode: "tui",
    session: { kind: "new" },
    systemPromptPath: "/abs/prompt.md",
    toggles: {
      skills: false,
      contextFiles: false,
      extensions: false,
      builtinTools: false,
    },
    provider: "anthropic",
    modelName: "claude-sonnet-4-20250514",
    ...overrides,
  };
}

const fakeModel = { provider: "anthropic", id: "x", name: "x" } as never;
const fakeTools: ToolDefinition[] = [
  {
    name: "balance",
    label: "b",
    description: "b",
    parameters: {} as never,
    execute: async () => ({ content: [], details: {} }),
  },
];

describe("buildSessionOptions: resourceLoaderOptions", () => {
  it("sets systemPrompt to the resolved system prompt string", () => {
    const opts = buildSessionOptions(makeConfig(), fakeTools, fakeModel, "You are a trader.");
    expect(opts.servicesOptions.resourceLoaderOptions?.systemPrompt).toBe("You are a trader.");
  });

  it("suppresses skills, context files, and extensions by default", () => {
    const opts = buildSessionOptions(makeConfig(), fakeTools, fakeModel, "x");
    expect(opts.servicesOptions.resourceLoaderOptions?.noSkills).toBe(true);
    expect(opts.servicesOptions.resourceLoaderOptions?.noContextFiles).toBe(true);
    expect(opts.servicesOptions.resourceLoaderOptions?.noExtensions).toBe(true);
  });

  it("does NOT suppress skills/contextFiles/extensions when toggles are on", () => {
    const cfg = makeConfig({
      toggles: { skills: true, contextFiles: true, extensions: true, builtinTools: false },
    });
    const opts = buildSessionOptions(cfg, fakeTools, fakeModel, "x");
    expect(opts.servicesOptions.resourceLoaderOptions?.noSkills).toBe(false);
    expect(opts.servicesOptions.resourceLoaderOptions?.noContextFiles).toBe(false);
    expect(opts.servicesOptions.resourceLoaderOptions?.noExtensions).toBe(false);
  });

  it("always suppresses prompt templates and themes (not toggled)", () => {
    const opts = buildSessionOptions(makeConfig(), fakeTools, fakeModel, "x");
    expect(opts.servicesOptions.resourceLoaderOptions?.noPromptTemplates).toBe(true);
    expect(opts.servicesOptions.resourceLoaderOptions?.noThemes).toBe(true);
  });
});

describe("buildSessionOptions: sessionOptions (fromServices)", () => {
  it("uses noTools='builtin' when builtinTools toggle is off", () => {
    const opts = buildSessionOptions(makeConfig(), fakeTools, fakeModel, "x");
    expect(opts.sessionOptions.noTools).toBe("builtin");
    expect(opts.sessionOptions.tools).toBeUndefined();
  });

  it("uses the explicit 7-name tools allowlist when builtinTools is on", () => {
    const cfg = makeConfig({
      toggles: { skills: false, contextFiles: false, extensions: false, builtinTools: true },
    });
    const opts = buildSessionOptions(cfg, fakeTools, fakeModel, "x");
    expect(opts.sessionOptions.tools).toEqual([
      "read",
      "bash",
      "edit",
      "write",
      "grep",
      "find",
      "ls",
    ]);
    expect(opts.sessionOptions.noTools).toBeUndefined();
  });

  it("passes customTools through unchanged", () => {
    const opts = buildSessionOptions(makeConfig(), fakeTools, fakeModel, "x");
    expect(opts.sessionOptions.customTools).toBe(fakeTools);
    expect(opts.sessionOptions.customTools?.length).toBeGreaterThan(0);
  });

  it("passes the model through", () => {
    const opts = buildSessionOptions(makeConfig(), fakeTools, fakeModel, "x");
    expect(opts.sessionOptions.model).toBe(fakeModel);
  });
});

describe("buildSessionManager", () => {
  it("returns SessionManager.create(cwd) for kind='new'", async () => {
    const { SessionManager } = await import("@earendil-works/pi-coding-agent");
    const { buildSessionManager } = await import("../src/tui.js");
    const sm = buildSessionManager({ kind: "new" }, "/repo");
    expect(sm.constructor.name).toBe(SessionManager.create("/repo").constructor.name);
  });
});
