/**
 * End-to-end test: spin up a real Python AgentGateway on a free port, drive
 * the full TS forwarder against it (createTools + dispatch + subscribe),
 * and assert the round-trip works.
 *
 * Runs `uv run python tests/e2e/run-gateway.py` in beforeAll, captures the
 * port from its first stdout line, waits for the socket to accept
 * connections, then exercises the public API.
 */

import { type ChildProcess, spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createTools, subscribeEvents } from "../../src/index.js";
import type { BusEvent } from "../../src/types.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..", "..");
const HARNESS = path.join(HERE, "run-gateway.py");

let gatewayProcess: ChildProcess | undefined;
let gatewayUrl = "";

async function startGateway() {
  if (!existsSync(HARNESS)) {
    throw new Error(`harness script not found at ${HARNESS}`);
  }
  gatewayProcess = spawn("uv", ["run", "python", HARNESS], {
    cwd: REPO,
    stdio: ["ignore", "pipe", "pipe"],
  });

  // Surface stderr if startup fails — the test fails with the Python traceback
  // instead of a hung "fetch failed".
  let stderrBuf = "";
  gatewayProcess.stderr?.on("data", (chunk: Buffer) => {
    stderrBuf += chunk.toString();
  });
  gatewayProcess.on("exit", (code) => {
    if (code !== 0 && code !== null) {
      // Keep this quiet on normal SIGTERM cleanup; loud on unexpected exit.
      if (code !== 143 /* SIGTERM */) {
        // eslint-disable-next-line no-console
        console.error(`[harness] exited with code ${code}:\n${stderrBuf}`);
      }
    }
  });

  const stdout = gatewayProcess.stdout;
  if (!stdout) throw new Error("harness stdout is null");

  const rl = readline.createInterface({ input: stdout });
  const [line] = (await once(rl, "line")) as [string];
  rl.close();
  const { port } = JSON.parse(line) as { port: number };
  if (typeof port !== "number") {
    throw new Error(`harness did not print a numeric port, got: ${line}`);
  }
  gatewayUrl = `http://127.0.0.1:${port}`;

  // Wait for the socket to accept connections (uvicorn binds, then
  // initialises; the JSON line above is printed after binding but before
  // the loop is fully serving. A short poll loop is the most honest check.)
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${gatewayUrl}/tools`);
      if (res.ok) {
        await res.body?.cancel();
        return;
      }
    } catch {
      // not ready yet
    }
    await sleep(50);
  }
  throw new Error(`gateway at ${gatewayUrl} did not start within 5s`);
}

async function stopGateway() {
  if (!gatewayProcess) return;
  const proc = gatewayProcess;
  gatewayProcess = undefined;
  proc.kill("SIGTERM");
  // Give it a moment to shut down cleanly; then SIGKILL if it's still there.
  const exited = await Promise.race([
    once(proc, "exit").then(() => true),
    sleep(2000).then(() => false),
  ]);
  if (!exited) proc.kill("SIGKILL");
  await once(proc, "exit").catch(() => {});
}

beforeAll(async () => {
  await startGateway();
}, 20_000);

afterAll(async () => {
  await stopGateway();
});

describe("end-to-end: TS forwarder against a real Python AgentGateway", () => {
  it("createTools fetches the live catalog and returns AgentTools", async () => {
    const tools = await createTools({ gatewayUrl });

    const names = tools.map((t) => t.name);
    // The stub account service advertises all four account tools.
    expect(names).toEqual(
      expect.arrayContaining(["get_balance", "get_positions", "place_order", "cancel_order"]),
    );

    const balance = tools.find((t) => t.name === "get_balance");
    expect(balance?.description).toMatch(/balance/i);
    expect(balance?.label).toBe("get_balance");
  });

  it("a tool's execute() round-trips through /dispatch to the live gateway", async () => {
    const tools = await createTools({ gatewayUrl });
    const getBalance = tools.find((t) => t.name === "get_balance");
    if (!getBalance) throw new Error("expected a get_balance tool in the live catalog");

    const result = await getBalance.execute("call-1", {});

    // The stub AccountService returns a Balance; the gateway serializes it
    // through `ToolLayer._serialize`, so the LLM-facing text is JSON.
    expect(result.details).toMatchObject({
      cash: "1000",
      equity: "1500",
      buying_power: "2000",
    });
    const first = result.content[0];
    if (first?.type !== "text") throw new Error("expected text content from get_balance");
    expect(first.text).toContain('"cash"');
  });

  it("subscribeEvents connects to /stream and yields typed BusEvents", async () => {
    // The stub bus has no publishers, so the connection is open but the
    // stream stays empty. We just verify the consumer can open it and
    // break out cleanly within a short window.
    const events: BusEvent[] = [];
    const collect = (async () => {
      for await (const e of subscribeEvents({ gatewayUrl, events: ["fill"] })) {
        events.push(e);
        if (events.length > 0) break;
      }
    })();

    // No events will arrive (nothing publishes), so race against a timeout.
    await Promise.race([collect, sleep(500)]);
    expect(events).toEqual([]);
  });
});
