/**
 * config — pure resolver for the runner's CLI + env inputs.
 *
 * Kept separate from main.ts so the resolution logic is unit-testable
 * without spawning a subprocess. The runner does I/O (load .env, print
 * errors, exit); this module just decides what to do.
 *
 * Conventions (Claude Code / Codex style):
 *   --provider <name>             # flag selects the provider
 *   <PROVIDER>_API_KEY           # env, read by pi-ai natively
 *   <PROVIDER>_BASE_URL          # env, OPTIONAL, applied as model.baseUrl override
 *   <PROVIDER>_MODEL             # env, REQUIRED, passed to getModel()
 *
 * Provider -> env-prefix mapping for the API key. The "google" provider
 * is the one known exception (pi-ai uses GEMINI_API_KEY, not GOOGLE_API_KEY);
 * we mirror that so users reading the pi-ai docs land on the right name.
 */

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

export interface ResolvedConfig {
  provider: string;
  modelName: string;
  apiKey?: string;
  baseUrl?: string;
  prompt: string;
}

/** Map a provider name to the env-var prefix used for the API key.
 *  Mirrors pi-ai's `getEnvApiKey` mapping for the providers we care about. */
function providerEnvPrefix(provider: string): string {
  switch (provider) {
    case "google":
      return "GEMINI";
    default:
      return provider.toUpperCase().replace(/-/g, "_");
  }
}

/** Walk argv, splitting into flags (--provider <value>) and positional (the prompt). */
function splitArgs(argv: string[]): { flags: string[]; positional: string[] } {
  const flags: string[] = [];
  const positional: string[] = [];
  let i = 0;
  while (i < argv.length) {
    const current = argv[i];
    if (current === undefined) break; // satisfies the noUncheckedIndexedAccess rule
    if (current === "--provider") {
      const next = argv[i + 1];
      if (next === undefined) {
        throw new ConfigError("--provider requires a value (e.g., --provider openai)");
      }
      if (next.startsWith("--")) {
        throw new ConfigError(`--provider requires a value, got another flag: ${next}`);
      }
      flags.push(current, next);
      i += 2;
      continue;
    }
    if (current.startsWith("--")) {
      throw new ConfigError(`unknown flag: ${current}`);
    }
    positional.push(current);
    i += 1;
  }
  return { flags, positional };
}

function parseProviderFlag(flags: string[]): string | undefined {
  const idx = flags.indexOf("--provider");
  if (idx === -1) return undefined;
  return flags[idx + 1];
}

/** Resolve the full runner config from argv + env. Pure: no I/O, no side effects. */
export function resolveConfig(argv: string[], env: NodeJS.ProcessEnv): ResolvedConfig {
  const { flags, positional } = splitArgs(argv);
  const provider = parseProviderFlag(flags) ?? "anthropic";
  const prefix = providerEnvPrefix(provider);

  const apiKey = env[`${prefix}_API_KEY`];
  const baseUrl = env[`${prefix}_BASE_URL`];
  const modelName = env[`${prefix}_MODEL`];

  // Fail fast: model and key must be set BEFORE the LLM round-trip. pi-ai
  // would catch the missing key later (at first API call) but with a less
  // actionable error.
  if (!modelName) {
    throw new ConfigError(`missing ${prefix}_MODEL in env (set it in repo .env or your shell)`);
  }
  if (!apiKey) {
    throw new ConfigError(`missing ${prefix}_API_KEY in env (set it in repo .env or your shell)`);
  }

  const prompt = positional.join(" ").trim();
  if (!prompt) {
    throw new ConfigError("usage: agent [--provider <anthropic|openai|google>] <prompt>");
  }

  const config: ResolvedConfig = { provider, modelName, prompt };
  if (apiKey !== undefined) config.apiKey = apiKey;
  if (baseUrl !== undefined) config.baseUrl = baseUrl;
  return config;
}
