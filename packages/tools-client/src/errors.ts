/**
 * Error types for the AgentGateway seam. These mirror the gateway's error
 * mapping at the HTTP boundary:
 *   RiskRejected -> 400 {error:"risk_rejected", reason, rule}
 *   ValueError   -> 400 {error:"bad_request",   reason}
 *   other        -> 500 (no special mapping; the raw error is logged server-side)
 */

/** Thrown when the gateway returns 400 with `error: "risk_rejected"`.
 *  The agent can react to a denied order without parsing a traceback. */
export class RiskRejectedError extends Error {
  public readonly reason: string;
  public readonly rule: string;

  constructor(reason: string, rule: string) {
    super(`Risk rejected: ${reason} (rule: ${rule})`);
    this.name = "RiskRejectedError";
    this.reason = reason;
    this.rule = rule;
  }
}

/** Thrown when the gateway returns 400 with `error: "bad_request"`. */
export class BadRequestError extends Error {
  public readonly reason: string;

  constructor(reason: string) {
    super(`Bad request: ${reason}`);
    this.name = "BadRequestError";
    this.reason = reason;
  }
}
