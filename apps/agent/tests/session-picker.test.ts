import type { SessionInfo } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";
import { pickSession } from "../src/session-picker.js";

function fakeSessions(): SessionInfo[] {
  return [
    { path: "/s/a.jsonl", id: "a", cwd: "/x", messageCount: 1, firstMessage: "first" } as SessionInfo,
    { path: "/s/b.jsonl", id: "b", cwd: "/x", messageCount: 2, firstMessage: "second" } as SessionInfo,
    { path: "/s/c.jsonl", id: "c", cwd: "/x", messageCount: 3, firstMessage: "third" } as SessionInfo,
  ];
}

describe("pickSession", () => {
  it("returns the path of the chosen session (1-indexed)", async () => {
    const chosen = await pickSession(fakeSessions(), async () => "2");
    expect(chosen).toBe("/s/b.jsonl");
  });

  it("returns null for empty input (cancel)", async () => {
    const chosen = await pickSession(fakeSessions(), async () => "");
    expect(chosen).toBeNull();
  });

  it("returns null for 'q' input (cancel)", async () => {
    const chosen = await pickSession(fakeSessions(), async () => "q");
    expect(chosen).toBeNull();
  });

  it("returns null for 'Q' input (cancel, case-insensitive)", async () => {
    const chosen = await pickSession(fakeSessions(), async () => "Q");
    expect(chosen).toBeNull();
  });

  it("returns null for out-of-range input (0)", async () => {
    const chosen = await pickSession(fakeSessions(), async () => "0");
    expect(chosen).toBeNull();
  });

  it("returns null for out-of-range input (4 in a 3-item list)", async () => {
    const chosen = await pickSession(fakeSessions(), async () => "4");
    expect(chosen).toBeNull();
  });

  it("returns null for non-numeric input", async () => {
    const chosen = await pickSession(fakeSessions(), async () => "abc");
    expect(chosen).toBeNull();
  });

  it("formats the prompt with a header showing total count and a numbered list", async () => {
    let captured = "";
    await pickSession(fakeSessions(), async (p) => {
      captured = p;
      return "1";
    });
    expect(captured).toMatch(/3 session/);
    expect(captured).toMatch(/1\..*first/s);
    expect(captured).toMatch(/2\..*second/s);
    expect(captured).toMatch(/3\..*third/s);
  });
});
