import { describe, expect, it } from "vitest";
import { ConfigError, resolveConfig } from "../src/config.js";

const baseEnv = {
  ANTHROPIC_API_KEY: "sk-ant-test",
  ANTHROPIC_MODEL: "claude-sonnet-4-20250514",
  OPENAI_API_KEY: "sk-openai-test",
  OPENAI_MODEL: "gpt-4o",
  GEMINI_API_KEY: "gemini-test",
  GEMINI_MODEL: "gemini-2.0-flash",
};

describe("resolveConfig: provider selection", () => {
  it("defaults to anthropic when no --provider flag is given", () => {
    const c = resolveConfig(["-p", "hi"], baseEnv);
    expect(c.provider).toBe("anthropic");
  });

  it("uses --provider openai (two-token form)", () => {
    const c = resolveConfig(["--provider", "openai", "-p", "hi"], baseEnv);
    expect(c.provider).toBe("openai");
  });

  it("uses --provider google and maps to GEMINI_* env vars", () => {
    // pi-ai uses GEMINI_API_KEY for the "google" provider, not GOOGLE_API_KEY.
    // The resolver must mirror that mapping so users reading pi-ai docs land
    // on the right var.
    const c = resolveConfig(["--provider", "google", "-p", "hi"], baseEnv);
    expect(c.provider).toBe("google");
    expect(c.apiKey).toBe("gemini-test");
    expect(c.modelName).toBe("gemini-2.0-flash"); // pulled from GEMINI_MODEL
  });

  it("rejects --provider=<value> (single-token form) as an unknown flag", () => {
    // We chose two-token only. --provider=openai looks like a different
    // flag ("--provider=openai") and should be rejected.
    expect(() => resolveConfig(["--provider=openai", "-p", "hi"], baseEnv)).toThrow(/unknown flag/);
  });
});

describe("resolveConfig: flag validation", () => {
  it("throws if --provider is the last arg with no value", () => {
    expect(() => resolveConfig(["--provider"], baseEnv)).toThrow(ConfigError);
    expect(() => resolveConfig(["--provider"], baseEnv)).toThrow(/--provider requires a value/);
  });

  it("throws if --provider is followed by another flag", () => {
    expect(() => resolveConfig(["--provider", "--verbose"], baseEnv)).toThrow(/requires a value/);
  });

  it("throws on unknown --flags", () => {
    expect(() => resolveConfig(["--unknown", "-p", "hi"], baseEnv)).toThrow(/unknown flag/);
  });
});

describe("resolveConfig: env var resolution", () => {
  it("reads ANTHROPIC_API_KEY and ANTHROPIC_MODEL", () => {
    const c = resolveConfig(["-p", "hi"], baseEnv);
    expect(c.apiKey).toBe("sk-ant-test");
    expect(c.modelName).toBe("claude-sonnet-4-20250514");
  });

  it("reads OPENAI_API_KEY and OPENAI_MODEL when --provider openai", () => {
    const c = resolveConfig(["--provider", "openai", "-p", "hi"], baseEnv);
    expect(c.apiKey).toBe("sk-openai-test");
    expect(c.modelName).toBe("gpt-4o");
  });

  it("returns baseUrl if set", () => {
    const c = resolveConfig(["-p", "hi"], {
      ...baseEnv,
      ANTHROPIC_BASE_URL: "https://proxy.example.com",
    });
    expect(c.baseUrl).toBe("https://proxy.example.com");
  });

  it("omits baseUrl when not set", () => {
    const c = resolveConfig(["-p", "hi"], baseEnv);
    expect(c.baseUrl).toBeUndefined();
  });

  it("throws if the API key env var is missing", () => {
    expect(() => resolveConfig(["-p", "hi"], { ANTHROPIC_MODEL: "x" })).toThrow(
      /missing ANTHROPIC_API_KEY/,
    );
  });

  it("throws if the model env var is missing", () => {
    expect(() => resolveConfig(["-p", "hi"], { ANTHROPIC_API_KEY: "x" })).toThrow(
      /missing ANTHROPIC_MODEL/,
    );
  });

  it("throws if the wrong provider's env vars are set", () => {
    // Common bug: user sets OPENAI_* but forgets --provider openai. The
    // default anthropic provider then complains about its own missing vars.
    // We accept either ANTHROPIC_* missing var as the error — the test is
    // about catching the bug, not about which var is checked first.
    expect(() =>
      resolveConfig(["-p", "hi"], { OPENAI_API_KEY: "x", OPENAI_MODEL: "gpt-4o" }),
    ).toThrow(/missing ANTHROPIC_/);
  });
});

describe("resolveConfig: prompt assembly", () => {
  it("throws on positional args (use -p instead)", () => {
    expect(() => resolveConfig(["positional"], baseEnv)).toThrow(/unexpected positional/);
  });
});

// --- New tests for the TUI/headless split ---

describe("resolveConfig: mode and session derivation", () => {
  it("no args -> mode tui, session new", () => {
    const c = resolveConfig([], baseEnv);
    expect(c.mode).toBe("tui");
    expect(c.session).toEqual({ kind: "new" });
  });

  it("-c -> mode tui, session continue", () => {
    const c = resolveConfig(["-c"], baseEnv);
    expect(c.mode).toBe("tui");
    expect(c.session).toEqual({ kind: "continue" });
  });

  it("-r -> mode tui, session r-flag-set (path picked later)", () => {
    const c = resolveConfig(["-r"], baseEnv);
    expect(c.mode).toBe("tui");
    expect(c.session).toEqual({ kind: "resume" });
  });

  it("-p <prompt> -> mode headless, prompt set", () => {
    const c = resolveConfig(["-p", "what is my balance?"], baseEnv);
    expect(c.mode).toBe("headless");
    expect(c.prompt).toBe("what is my balance?");
  });

  it("-p with no following arg -> ConfigError", () => {
    expect(() => resolveConfig(["-p"], baseEnv)).toThrow(ConfigError);
    expect(() => resolveConfig(["-p"], baseEnv)).toThrow(/-p requires a value/);
  });

  it("-p followed by another flag -> ConfigError", () => {
    expect(() => resolveConfig(["-p", "--c"], baseEnv)).toThrow(/-p requires a value/);
  });

  it("-p with -c -> ConfigError (mutually exclusive)", () => {
    expect(() => resolveConfig(["-p", "hi", "-c"], baseEnv)).toThrow(
      /cannot combine -p with -c or -r/,
    );
  });

  it("-p with -r -> ConfigError (mutually exclusive)", () => {
    expect(() => resolveConfig(["-r", "-p", "hi"], baseEnv)).toThrow(
      /cannot combine -p with -c or -r/,
    );
  });

  it("-c with -r -> ConfigError (mutually exclusive)", () => {
    expect(() => resolveConfig(["-c", "-r"], baseEnv)).toThrow(/cannot combine/);
  });
});

describe("resolveConfig: system prompt path", () => {
  it("defaults to <repo>/apps/agent/system-prompt.md when env unset", () => {
    const c = resolveConfig([], baseEnv);
    // The exact value depends on the dev's repo location, but it must end
    // with the canonical relative path.
    expect(c.systemPromptPath.endsWith("apps/agent/system-prompt.md")).toBe(true);
  });

  it("respects absolute AGENT_SYSTEM_PROMPT_FILE", () => {
    const c = resolveConfig([], { ...baseEnv, AGENT_SYSTEM_PROMPT_FILE: "/abs/prompt.md" });
    expect(c.systemPromptPath).toBe("/abs/prompt.md");
  });

  it("resolves relative AGENT_SYSTEM_PROMPT_FILE against repo root", () => {
    const c = resolveConfig([], { ...baseEnv, AGENT_SYSTEM_PROMPT_FILE: "docs/x.md" });
    expect(c.systemPromptPath.endsWith("/docs/x.md")).toBe(true);
    expect(c.systemPromptPath.startsWith("/")).toBe(true);
  });
});

describe("resolveConfig: toggle env vars", () => {
  it("all toggles default to false when env unset", () => {
    const c = resolveConfig([], baseEnv);
    expect(c.toggles).toEqual({
      skills: false,
      contextFiles: false,
      extensions: false,
      builtinTools: false,
    });
  });

  it.each([
    ["true", true],
    ["1", true],
    ["yes", true],
    ["on", true],
    ["TRUE", true],
    ["Yes", true],
  ])("AGENT_ENABLE_SKILLS=%s -> toggles.skills = %s", (val, expected) => {
    const c = resolveConfig([], { ...baseEnv, AGENT_ENABLE_SKILLS: val });
    expect(c.toggles.skills).toBe(expected);
  });

  it.each([["false", false], ["0", false], ["", false], ["nope", false], ["random", false]])(
    "AGENT_ENABLE_SKILLS=%s -> toggles.skills = false",
    (val) => {
      const c = resolveConfig([], { ...baseEnv, AGENT_ENABLE_SKILLS: val });
      expect(c.toggles.skills).toBe(false);
    },
  );

  it("AGENT_ENABLE_CONTEXT_FILES=true -> toggles.contextFiles = true", () => {
    const c = resolveConfig([], { ...baseEnv, AGENT_ENABLE_CONTEXT_FILES: "true" });
    expect(c.toggles.contextFiles).toBe(true);
  });

  it("AGENT_ENABLE_EXTENSIONS=true -> toggles.extensions = true", () => {
    const c = resolveConfig([], { ...baseEnv, AGENT_ENABLE_EXTENSIONS: "true" });
    expect(c.toggles.extensions).toBe(true);
  });

  it("AGENT_ENABLE_BUILTIN_TOOLS=true -> toggles.builtinTools = true", () => {
    const c = resolveConfig([], { ...baseEnv, AGENT_ENABLE_BUILTIN_TOOLS: "true" });
    expect(c.toggles.builtinTools).toBe(true);
  });
});
