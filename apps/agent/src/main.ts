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
import { runTui } from "./tui.js";

// .env loading: walk up from this file to find the repo root, then load
// `.env` from there if present. Explicit shell env always wins over .env.
const envPath = resolve(findRepoRoot(import.meta.dirname), ".env");
if (existsSync(envPath)) {
	dotenvConfig({ path: envPath });
}

let resolved: ResolvedConfig;
try {
	// The justfile invokes us via `pnpm start -- {{args}}`. pnpm passes the
	// `--` through to the script, which would otherwise look like an unknown
	// flag to our argv parser. Strip a single leading `--` separator.
	const argv = process.argv.slice(2);
	const stripped = argv[0] === "--" ? argv.slice(1) : argv;
	resolved = resolveConfig(stripped, process.env);
} catch (e) {
	if (e instanceof ConfigError) {
		console.error(`[agent] ${e.message}`);
		process.exit(2);
	}
	throw e;
}

if (resolved.mode === "headless") {
	await runHeadless(resolved);
} else {
	await runTui(resolved);
}
