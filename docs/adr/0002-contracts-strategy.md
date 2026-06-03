# ADR-0002: Contracts Strategy — Python SOT + TS Hand-Written Mirror + Test Guard

## Context

The AgentGateway (`apps/live/pi_gateway.py`) is the cross-language HTTP
seam between the Python trading platform and the TypeScript forwarder in
`apps/agent/src/forwarder/`. Both ends must agree on three wire shapes:

- `GET /tools` — `list[ToolSpec]`
- `POST /dispatch` — `DispatchRequest` body, arbitrary response
- `GET /stream` — `Event` envelope (SSE), one data line per occurrence

The question is how to keep the two sides in sync.

We considered four approaches:

| | Approach | When it pays off |
|---|---|---|
| (a) | Python SOT + TS hand-written mirror + Python guard test | Small, stable surface; one team owns both ends |
| (b) | (a) + ajv runtime validation on the TS side | Drift risk between ends controlled by different teams |
| (c) | (a) + json-schema-to-ts build-time type generation | Large TS type surface; contracts change frequently |
| (d) | The current state (Pydantic emits JSON Schemas; nothing reads them) | Never — the appearance of a contract without enforcement is misleading |

## Decision

**Adopt (a).**

- **Pydantic models in `packages/contracts/src/contracts/gateway.py` are the
  single source of truth** for the wire shapes: `ToolSpec`,
  `DispatchRequest`, and `BusEvent` (aliased as `Event[dict[str, Any]]`
  from `contracts.schema`).
- **The TS side maintains hand-written equivalents** in
  `apps/agent/src/forwarder/types.ts`. They mirror the Pydantic shapes
  by hand and are updated in the same commit as any Pydantic change.
  The forwarder lives inside the agent app (one consumer today) rather
  than as a separate package — see the Apps section below.
- **A Python guard test in `tests/integration/test_pi_gateway.py` asserts
  real gateway responses validate against the Pydantic models.** Adding
  a required field to `ToolSpec`, removing a field, or having the
  gateway return a field not in the model all make the test fail.
  `ToolSpec` carries `extra="forbid"` specifically so the "extra field"
  direction is caught.
- **`scripts/generate_contracts.py` and `contracts/*.schema.json` are
  removed.** `just gen-contracts*` targets are removed.

## Why not (b)?

ajv (or equivalent) runtime validation on the TS side would defend against
schema drift in production, but adds a per-response validation cost on
every `/tools` and `/dispatch` call. Both ends of the seam are owned by
the same team and the surface is small — the drift risk is contained
by the Python guard test alone. Revisit if a second TS consumer
appears or the surfaces diverge.

## Why not (c)?

`json-schema-to-ts` (or any build-time codegen) would replace the
hand-written `src/types.ts` with derived types, giving a single SOT
across both languages. The payoff is when (i) the TS type surface is
large enough that hand-mirroring is a burden, AND (ii) the contracts
change frequently enough that the mirror churns noticeably. Today the
TS surface is ~3 small interfaces and the contracts are stable.

When either condition is met, (c) is the next step. The transition is
non-breaking: the Pydantic models already exist, the generator script
can be revived from git history (`48c7860`–`1e550af` on
`feat/ts-tools-client`), and `src/types.ts` can be replaced with
derived types without touching the public API.

## Why not (d)?

The previous state — Pydantic generates JSON Schemas, the TS side
ignores them — creates the *appearance* of a contract without any
enforcement. Anyone reading the repo would reasonably assume the JSON
Schema files are authoritative; in practice they were dead weight and
a maintenance liability (a Pydantic change requires regenerating a
file that no code parses).

## Consequences

- **One fewer moving part** in the repo: no codegen script, no
  generated `contracts/` directory, no `just gen-contracts*` targets.
- **`apps/agent/src/forwarder/types.ts` becomes a load-bearing mirror.**
  Drift between it and the Pydantic models is caught only when Python
  changes — the Python guard test fires before the TS side has had a
  chance to drift independently. If a TS-side type widens (e.g.,
  relaxes a required field), the Python test won't catch it; the
  round-trip e2e test (`apps/agent/tests/e2e/`) is the second line of
  defense.
- **The `pyrefly: ignore [missing-import]` markers and other
  "cross-language model" aspirations remain aspirational.** This ADR
  codifies that we don't have the machinery for them yet, on purpose.
- **Re-introducing codegen later is cheap.** All the pieces (generator
  script, schema files, justfile targets) are in git history; reviving
  them is a `git revert` plus re-installation of the corresponding test
  suite, not a fresh design.

## Apps

The TS forwarder lives inside the agent app (`apps/agent/src/forwarder/`)
rather than as a separate library, because there is exactly one consumer
(the agent runner in `apps/agent/src/main.ts`). If a second consumer
appears — a CLI, a dashboard, a different agent variant — the forwarder
should be extracted into a `clients/` top-level zone at that point. The
extraction is a small refactor (move the directory, add a `package.json`
and `tsconfig.json`, add a pnpm workspace), not a rewrite.
