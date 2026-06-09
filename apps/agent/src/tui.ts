/**
 * tui — Interactive TUI driver for the trading agent.
 *
 * Public surface:
 *   runTui(config)                  — full TUI boot (does I/O: .env, gateway,
 *                                    readline, file I/O). Async.
 *   buildSessionOptions(...)        — pure: option-bag construction. Tested
 *                                    in tui-wiring.test.ts. Exported so the
 *                                    wiring test can lock the shape.
 *   buildSessionManager(spec, cwd)  — pure: maps SessionSpec to a
 *                                    SessionManager. Tested in
 *                                    tui-wiring.test.ts.
 *
 * The wiring mirrors the SDK's own CLI bootstrap (see
 * pi-coding-agent/src/main.ts:617-786): services → fromServices → runtime
 * factory → runtime → InteractiveMode. The customRuntime factory is
 * re-invoked by the framework for /new, /resume, /fork, so we close over
 * the long-lived inputs (config, customTools, systemPrompt, cwd) and let
 * the framework call it again with the effective cwd and sessionManager.
 */

import { createInterface } from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import {
  type CreateAgentSessionFromServicesOptions,
  type CreateAgentSessionRuntimeFactory,
  type CreateAgentSessionServicesOptions,
  createAgentSessionFromServices,
  createAgentSessionRuntime,
  createAgentSessionServices,
  getAgentDir,
  InteractiveMode,
  type SessionInfo,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
import { createTools } from "./forwarder/index.js";
import { buildModel, ConfigError, loadSystemPrompt } from "./runner.js";
import type { ResolvedConfig, SessionSpec } from "./config.js";
import { findRepoRoot } from "./repo.js";
import { pickSession } from "./session-picker.js";
import { wrapAgentTool } from "./tool-adapter.js";

const GATEWAY_URL_ENV = "GATEWAY_URL";
const DEFAULT_GATEWAY_URL = "http://127.0.0.1:8787";

export interface BuiltSessionOptions {
  servicesOptions: CreateAgentSessionServicesOptions;
  sessionOptions: Omit<
    CreateAgentSessionFromServicesOptions,
    "services" | "sessionManager" | "sessionStartEvent"
  >;
}

/**
 * Pure: construct the option bags that we'll pass to the SDK.
 * `systemPrompt` is the *contents* of the file (already loaded), not the path.
 * Tested by tui-wiring.test.ts; if you change the shape, update the test.
 */
export function buildSessionOptions(
  config: ResolvedConfig,
  customTools: Parameters<typeof createAgentSessionFromServices>[0]["customTools"],
  model: Parameters<typeof createAgentSessionFromServices>[0]["model"],
  systemPrompt: string,
): BuiltSessionOptions {
  const servicesOptions: CreateAgentSessionServicesOptions = {
    cwd: findRepoRoot(process.cwd()),
    agentDir: getAgentDir(),
    resourceLoaderOptions: {
      noExtensions: !config.toggles.extensions,
      noSkills: !config.toggles.skills,
      noContextFiles: !config.toggles.contextFiles,
      noPromptTemplates: true,
      noThemes: true,
      systemPrompt,
    },
  };

  const sessionOptions: Omit<
    CreateAgentSessionFromServicesOptions,
    "services" | "sessionManager" | "sessionStartEvent"
  > = {
    model,
    customTools,
    ...(config.toggles.builtinTools
      ? { tools: ["read", "bash", "edit", "write", "grep", "find", "ls"] }
      : { noTools: "builtin" as const }),
  };

  return { servicesOptions, sessionOptions };
}

/**
 * Pure: map a SessionSpec + cwd to a SessionManager.
 * - "new"      → SessionManager.create(cwd)
 * - "continue" → SessionManager.continueRecent(cwd); if no prior session,
 *                throw ConfigError so the caller can exit 1 with a message.
 * - "resume"   → SessionManager.open(<path from picker>)
 */
export function buildSessionManager(
  spec: SessionSpec,
  cwd: string,
  options?: { resumedPath?: string },
): SessionManager {
  if (spec.kind === "new") {
    return SessionManager.create(cwd);
  }
  if (spec.kind === "continue") {
    const sm = SessionManager.continueRecent(cwd);
    // The framework returns a fresh manager with no session file when
    // there is no prior session. Detect that and surface a clear error.
    if (!sm.getSessionFile()) {
      throw new ConfigError(
        "-c: no prior session found for this project (run without -c to start a new session)",
      );
    }
    return sm;
  }
  // spec.kind === "resume"
  if (!options?.resumedPath) {
    throw new ConfigError("-r: internal error: resumedPath not provided");
  }
  return SessionManager.open(options.resumedPath);
}

/**
 * Run the interactive TUI. Loads the system prompt, fetches the gateway
 * tools, builds the SDK session runtime, and hands control to the
 * framework's InteractiveMode. Returns when the user exits the TUI.
 */
export async function runTui(config: ResolvedConfig): Promise<void> {
  const cwd = findRepoRoot(process.cwd());
  const agentDir = getAgentDir();
  const systemPrompt = await loadSystemPrompt(config.systemPromptPath);
  const model = buildModel(config);
  const gatewayUrl = process.env[GATEWAY_URL_ENV] ?? DEFAULT_GATEWAY_URL;

  // Handle -r before constructing the SessionManager — we need to ask
  // the user which session to resume.
  let resumedPath: string | undefined;
  if (config.session.kind === "resume") {
    const all = await SessionManager.listAll();
    const rl = createInterface({ input, output });
    try {
      const ask = async (prompt: string): Promise<string> => {
        const ans = await rl.question(prompt);
        return ans;
      };
      const chosen = await pickSession(all as readonly SessionInfo[], ask);
      if (chosen === null) {
        console.error("[agent] -r: no session selected, aborting");
        process.exit(1);
      }
      resumedPath = chosen;
    } finally {
      rl.close();
    }
  }

  const sessionManager = buildSessionManager(config.session, cwd, { resumedPath });

  // Load forwarder tools. Fail fast on gateway errors.
  const forwarderTools = await createTools({ gatewayUrl });
  const customTools = forwarderTools.map(wrapAgentTool);

  const { servicesOptions, sessionOptions } = buildSessionOptions(
    config,
    customTools,
    model,
    systemPrompt,
  );

  // The factory closes over the long-lived inputs and is re-invoked by
  // the framework for /new, /resume, /fork.
  const createRuntime: CreateAgentSessionRuntimeFactory = async ({
    cwd: effectiveCwd,
    agentDir: effectiveAgentDir,
    sessionManager: effectiveSm,
    sessionStartEvent,
  }) => {
    const services = await createAgentSessionServices({
      ...servicesOptions,
      cwd: effectiveCwd,
      agentDir: effectiveAgentDir,
    });
    const created = await createAgentSessionFromServices({
      services,
      sessionManager: effectiveSm,
      sessionStartEvent,
      ...sessionOptions,
    });
    return { ...created, services, diagnostics: services.diagnostics };
  };

  const runtime = await createAgentSessionRuntime(createRuntime, {
    cwd,
    agentDir,
    sessionManager,
  });

  // Surface error-level diagnostics before booting the TUI.
  const errors = runtime.diagnostics.filter((d) => d.type === "error");
  if (errors.length > 0) {
    for (const d of runtime.diagnostics) {
      console.error(`[agent] ${d.type}: ${d.message}`);
    }
    process.exit(1);
  }

  const mode = new InteractiveMode(runtime, {
    migratedProviders: [],
    modelFallbackMessage: runtime.modelFallbackMessage,
    initialMessage: undefined,
    initialImages: [],
    initialMessages: [],
  });

  await mode.run();
}
