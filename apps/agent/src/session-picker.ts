/**
 * session-picker — interactive picker for resuming a previous session.
 *
 * Pure function: takes a pre-fetched list of SessionInfo and a
 * question-asking callback, returns the chosen session's path or
 * null for cancel/out-of-range. The caller is responsible for:
 *   - calling SessionManager.listAll() to fetch the sessions
 *   - building the readline interface (we don't import node:readline here
 *     so this function is unit-testable without a TTY)
 *   - on null, exiting with an appropriate message
 *
 * The list is sorted by `modified` descending (most recent first). Each
 * line shows the index, the first user message preview, and the relative
 * time. The user types a number, 'q' to cancel, or hits enter to cancel.
 */

import type { SessionInfo } from "@earendil-works/pi-coding-agent";

export type AskFn = (prompt: string) => Promise<string>;

export async function pickSession(
  sessions: readonly SessionInfo[],
  ask: AskFn,
): Promise<string | null> {
  if (sessions.length === 0) {
    await ask("No sessions found. Press enter to abort.");
    return null;
  }

  const sorted = [...sessions].sort((a, b) => {
    const am = a.modified?.getTime() ?? 0;
    const bm = b.modified?.getTime() ?? 0;
    return bm - am;
  });

  const lines = sorted.map((s, i) => {
    const idx = i + 1;
    const preview = (s.firstMessage ?? "").replace(/\s+/g, " ").slice(0, 60);
    const when = s.modified ? formatRelative(s.modified) : "";
    return `  ${idx}. ${preview}${when ? `  (${when})` : ""}`;
  });
  const header = `${sorted.length} session${sorted.length === 1 ? "" : "s"} available:`;
  const prompt = `${header}\n${lines.join("\n")}\nSelect a session (1-${sorted.length}, q to cancel): `;

  const input = (await ask(prompt)).trim();
  if (input === "" || input.toLowerCase() === "q") return null;

  const n = Number.parseInt(input, 10);
  if (!Number.isFinite(n) || n < 1 || n > sorted.length) return null;

  const chosen = sorted[n - 1];
  return chosen?.path ?? null;
}

function formatRelative(date: Date): string {
  const diffMs = Date.now() - date.getTime();
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}
