/**
 * runner — shared loaders for headless and TUI modes.
 *
 *   loadSystemPrompt(path)  reads a markdown file and returns its contents.
 *   buildModel({...})       constructs a pi-ai Model from provider + name + baseUrl.
 *
 * Both functions are pure wrt the inputs they receive. The runner is
 * responsible for resolving paths and environment variables before calling
 * them; this module just does the I/O and the SDK glue.
 */

import { readFile } from "node:fs/promises";
import { getModel, getModels, type Model } from "@earendil-works/pi-ai";

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

/**
 * Read a system-prompt markdown file. Throws ConfigError on missing or
 * unreadable files. The error message is human-facing: it mentions the
 * path and the env var to set, so users get a clear next step.
 */
export async function loadSystemPrompt(absolutePath: string): Promise<string> {
  let raw: string;
  try {
    raw = await readFile(absolutePath, "utf8");
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      throw new ConfigError(
        `system prompt file not found: ${absolutePath} ` +
          `(set AGENT_SYSTEM_PROMPT_FILE or create apps/agent/system-prompt.md)`,
      );
    }
    throw new ConfigError(
      `system prompt file unreadable: ${absolutePath} (${(e as Error).message})`,
    );
  }
  return raw.trim();
}

export interface BuildModelOptions {
  provider: string;
  modelName: string;
  baseUrl?: string;
}

/**
 * Construct a pi-ai Model for the given provider + name. If the exact
 * model name isn't registered (e.g. for a fine-tune served by a local
 * proxy), clone the first registered template for the provider and
 * override `id`/`name` so the wire format is identical. If `baseUrl`
 * is set, mutate the returned model in place — pi-ai providers read
 * `model.baseUrl` when building the request.
 */
export function buildModel(opts: BuildModelOptions): Model<any> {
  let model = getModel(opts.provider as never, opts.modelName);
  if (!model) {
    const templates = getModels(opts.provider as never);
    const template = templates[0];
    if (!template) {
      throw new ConfigError(`provider ${opts.provider} has no registered models in pi-ai`);
    }
    model = { ...template, id: opts.modelName, name: opts.modelName };
  }
  if (opts.baseUrl) {
    model.baseUrl = opts.baseUrl;
  }
  return model;
}
