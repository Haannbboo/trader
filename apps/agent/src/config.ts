/**
 * config — pure resolver for the runner's CLI + env inputs.
 *
 * Kept separate from main.ts so the resolution logic is unit-testable
 * without spawning a subprocess. The runner does I/O (load .env, print
 * errors, exit); this module just decides what to do.
 *
 * Two modes share the resolver:
 *
 *   Headless (one-shot):  just agent -p "..."
 *     - prompt supplied via -p
 *     - mode = "headless"
 *     - no session, no TUI
 *
 *   TUI (interactive):  just agent  |  just agent -c  |  just agent -r
 *     - no prompt at the CLI (the user types in the TUI)
 *     - mode = "tui"
 *     - session = { kind: "new" } | { kind: "continue" } | { kind: "resume" }
 *
 * Toggles (env-driven, default false): AGENT_ENABLE_SKILLS,
 * AGENT_ENABLE_CONTEXT_FILES, AGENT_ENABLE_EXTENSIONS,
 * AGENT_ENABLE_BUILTIN_TOOLS. The system prompt path is env-driven via
 * AGENT_SYSTEM_PROMPT_FILE.
 *
 * Provider env vars follow the pi-ai convention: <PROVIDER>_API_KEY,
 * <PROVIDER>_MODEL, <PROVIDER>_BASE_URL. "google" maps to GEMINI_*.
 */

import { isAbsolute, resolve } from "node:path";
import { findRepoRoot } from "./repo.js";

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

export interface Toggles {
  skills: boolean;
  contextFiles: boolean;
  extensions: boolean;
  builtinTools: boolean;
}

export type SessionSpec =
  | { kind: "new" }
  | { kind: "continue" }
  | { kind: "resume" };

export type Mode = "headless" | "tui";

export interface ResolvedConfig {
  mode: Mode;
  session: SessionSpec;
  systemPromptPath: string;
  toggles: Toggles;
  // headless only
  prompt?: string;
  // model
  provider: string;
  modelName: string;
  apiKey?: string;
  baseUrl?: string;
}

/** Map a provider name to the env-var prefix used for the API key. */
function providerEnvPrefix(provider: string): string {
  switch (provider) {
    case "google":
      return "GEMINI";
    default:
      return provider.toUpperCase().replace(/-/g, "_");
  }
}

/** Parse a "truthy" env value: true/1/yes/on (case-insensitive) -> true; else false. */
function parseBool(value: string | undefined): boolean {
  if (!value) return false;
  const v = value.trim().toLowerCase();
  return v === "true" || v === "1" || v === "yes" || v === "on";
}

interface ParsedArgs {
  flags: string[];
  positional: string[];
}

/** Walk argv, splitting into flags (--provider, -p, -c, -r) and positional. */
function splitArgs(argv: string[]): ParsedArgs {
  const flags: string[] = [];
  const positional: string[] = [];
  let i = 0;
  while (i < argv.length) {
    const current = argv[i];
    if (current === undefined) break; // satisfies noUncheckedIndexedAccess
    if (current === "--provider" || current === "-p") {
      const next = argv[i + 1];
      if (next === undefined) {
        throw new ConfigError(`${current} requires a value`);
      }
      if (next.startsWith("--") || next.startsWith("-")) {
        throw new ConfigError(`${current} requires a value, got another flag: ${next}`);
      }
      flags.push(current, next);
      i += 2;
      continue;
    }
    if (current === "-c" || current === "-r") {
      flags.push(current);
      i += 1;
      continue;
    }
    if (current.startsWith("--") || current.startsWith("-")) {
      throw new ConfigError(`unknown flag: ${current}`);
    }
    positional.push(current);
    i += 1;
  }
  return { flags, positional };
}

function hasFlag(flags: string[], name: string): boolean {
  return flags.includes(name);
}

/** Resolve the system prompt path: env override (absolute or repo-relative) or default. */
function resolveSystemPromptPath(env: NodeJS.ProcessEnv): string {
  const fromEnv = env.AGENT_SYSTEM_PROMPT_FILE;
  if (fromEnv && fromEnv.trim().length > 0) {
    return isAbsolute(fromEnv) ? fromEnv : resolve(findRepoRoot(process.cwd()), fromEnv);
  }
  return resolve(findRepoRoot(process.cwd()), "apps/agent/system-prompt.md");
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

  if (!modelName) {
    throw new ConfigError(`missing ${prefix}_MODEL in env (set it in repo .env or your shell)`);
  }
  if (!apiKey) {
    throw new ConfigError(`missing ${prefix}_API_KEY in env (set it in repo .env or your shell)`);
  }

  const hasP = hasFlag(flags, "-p");
  const hasC = hasFlag(flags, "-c");
  const hasR = hasFlag(flags, "-r");

  if (hasP && (hasC || hasR)) {
    throw new ConfigError("cannot combine -p with -c or -r");
  }
  if (hasC && hasR) {
    throw new ConfigError("cannot combine -c with -r");
  }

  let mode: Mode;
  let session: SessionSpec;
  let prompt: string | undefined;

  if (hasP) {
    mode = "headless";
    session = { kind: "new" }; // unused for headless
    const pIdx = flags.indexOf("-p");
    const promptValue = flags[pIdx + 1];
    if (promptValue === undefined) {
      // splitArgs already throws for missing value, but keep the guard.
      throw new ConfigError("-p requires a value");
    }
    prompt = promptValue.trim();
    if (!prompt) {
      throw new ConfigError("-p requires a non-empty prompt");
    }
  } else {
    mode = "tui";
    if (hasC) {
      session = { kind: "continue" };
    } else if (hasR) {
      session = { kind: "resume" };
    } else {
      session = { kind: "new" };
    }
  }

  // Disallow positional args in any mode — -p is the only way to pass a prompt.
  if (positional.length > 0) {
    throw new ConfigError(
      `unexpected positional arg(s): ${positional.join(" ")} (use -p "<prompt>" for headless)`,
    );
  }

  const systemPromptPath = resolveSystemPromptPath(env);
  const toggles: Toggles = {
    skills: parseBool(env.AGENT_ENABLE_SKILLS),
    contextFiles: parseBool(env.AGENT_ENABLE_CONTEXT_FILES),
    extensions: parseBool(env.AGENT_ENABLE_EXTENSIONS),
    builtinTools: parseBool(env.AGENT_ENABLE_BUILTIN_TOOLS),
  };

  const config: ResolvedConfig = {
    mode,
    session,
    systemPromptPath,
    toggles,
    provider,
    modelName,
  };
  if (apiKey !== undefined) config.apiKey = apiKey;
  if (baseUrl !== undefined) config.baseUrl = baseUrl;
  if (prompt !== undefined) config.prompt = prompt;
  return config;
}
