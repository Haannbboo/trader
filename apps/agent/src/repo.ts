/**
 * repo — locate the trader monorepo root.
 *
 * The repo is a Python uv project; `pyproject.toml` is its marker file at
 * the root. Multiple apps in this monorepo (e.g. `apps/agent`, `apps/cli`)
 * need to find the root to load `.env`, resolve the default system-prompt
 * path, and scope sessions to the repo as a whole. This module centralizes
 * that walk so every caller gets the same answer.
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Walk up from `start` until a directory contains `pyproject.toml`, and
 * return that directory. If no such directory exists, return `start`
 * unchanged (the caller is expected to fail fast on a downstream
 * filesystem check).
 */
export function findRepoRoot(start: string): string {
  let dir = start;
  while (dir !== "/") {
    if (existsSync(resolve(dir, "pyproject.toml"))) return dir;
    dir = resolve(dir, "..");
  }
  return start;
}
