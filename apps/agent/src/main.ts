/**
 * apps/agent — the Pi Agent driver.
 *
 * Loads .env, parses CLI flags + env, and dispatches to the appropriate
 * mode (headless or TUI). Errors at config time exit 2; errors during
 * a run are surfaced by the runner.
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { config as dotenvConfig } from "dotenv";
import { ConfigError, type ResolvedConfig, resolveConfig } from "./config.js";
import { findRepoRoot } from "./repo.js";
import { runHeadless } from "./headless.js";

// .env loading: walk up from this file to find the repo root, then load
// `.env` from there if present. Explicit shell env always wins over .env.
const envPath = resolve(findRepoRoot(import.meta.dirname), ".env");
if (existsSync(envPath)) {
	dotenvConfig({ path: envPath });
}

let resolved: ResolvedConfig;
try {
	resolved = resolveConfig(process.argv.slice(2), process.env);
} catch (e) {
	if (e instanceof ConfigError) {
		console.error(`[agent] ${e.message}`);
		process.exit(2);
	}
	throw e;
}

if (resolved.mode === "headless") {
	await runHeadless(resolved);
	// TUI mode wiring lands in Task 9.
}
