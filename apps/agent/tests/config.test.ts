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
    const c = resolveConfig(["hi"], baseEnv);
    expect(c.provider).toBe("anthropic");
  });

  it("uses --provider openai (two-token form)", () => {
    const c = resolveConfig(["--provider", "openai", "hi"], baseEnv);
    expect(c.provider).toBe("openai");
  });

  it("uses --provider google and maps to GEMINI_* env vars", () => {
    // pi-ai uses GEMINI_API_KEY for the "google" provider, not GOOGLE_API_KEY.
    // The resolver must mirror that mapping so users reading pi-ai docs land
    // on the right var.
    const c = resolveConfig(["--provider", "google", "hi"], baseEnv);
    expect(c.provider).toBe("google");
    expect(c.apiKey).toBe("gemini-test");
    expect(c.modelName).toBe("gemini-2.0-flash"); // pulled from GEMINI_MODEL
  });

  it("rejects --provider=<value> (single-token form) as an unknown flag", () => {
    // We chose two-token only. --provider=openai looks like a different
    // flag ("--provider=openai") and should be rejected.
    expect(() => resolveConfig(["--provider=openai", "hi"], baseEnv)).toThrow(/unknown flag/);
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
    expect(() => resolveConfig(["--unknown", "hi"], baseEnv)).toThrow(/unknown flag/);
  });
});

describe("resolveConfig: env var resolution", () => {
  it("reads ANTHROPIC_API_KEY and ANTHROPIC_MODEL", () => {
    const c = resolveConfig(["hi"], baseEnv);
    expect(c.apiKey).toBe("sk-ant-test");
    expect(c.modelName).toBe("claude-sonnet-4-20250514");
  });

  it("reads OPENAI_API_KEY and OPENAI_MODEL when --provider openai", () => {
    const c = resolveConfig(["--provider", "openai", "hi"], baseEnv);
    expect(c.apiKey).toBe("sk-openai-test");
    expect(c.modelName).toBe("gpt-4o");
  });

  it("returns baseUrl if set", () => {
    const c = resolveConfig(["hi"], {
      ...baseEnv,
      ANTHROPIC_BASE_URL: "https://proxy.example.com",
    });
    expect(c.baseUrl).toBe("https://proxy.example.com");
  });

  it("omits baseUrl when not set", () => {
    const c = resolveConfig(["hi"], baseEnv);
    expect(c.baseUrl).toBeUndefined();
  });

  it("throws if the API key env var is missing", () => {
    expect(() => resolveConfig(["hi"], { ANTHROPIC_MODEL: "x" })).toThrow(
      /missing ANTHROPIC_API_KEY/,
    );
  });

  it("throws if the model env var is missing", () => {
    expect(() => resolveConfig(["hi"], { ANTHROPIC_API_KEY: "x" })).toThrow(
      /missing ANTHROPIC_MODEL/,
    );
  });

  it("throws if the wrong provider's env vars are set", () => {
    // Common bug: user sets OPENAI_* but forgets --provider openai. The
    // default anthropic provider then complains about its own missing vars.
    // We accept either ANTHROPIC_* missing var as the error — the test is
    // about catching the bug, not about which var is checked first.
    expect(() => resolveConfig(["hi"], { OPENAI_API_KEY: "x", OPENAI_MODEL: "gpt-4o" })).toThrow(
      /missing ANTHROPIC_/,
    );
  });
});

describe("resolveConfig: prompt assembly", () => {
  it("joins positional args into the prompt", () => {
    const c = resolveConfig(["what's", "my", "cash", "balance?"], baseEnv);
    expect(c.prompt).toBe("what's my cash balance?");
  });

  it("trims surrounding whitespace from the joined prompt", () => {
    const c = resolveConfig(["  hello world  "], baseEnv);
    expect(c.prompt).toBe("hello world");
  });

  it("throws with a usage hint when the prompt is empty", () => {
    expect(() => resolveConfig([], baseEnv)).toThrow(/usage: agent/);
  });

  it("preserves order even when --provider is mid-arg", () => {
    const c = resolveConfig(["--provider", "openai", "show", "positions"], baseEnv);
    expect(c.provider).toBe("openai");
    expect(c.prompt).toBe("show positions");
  });
});
