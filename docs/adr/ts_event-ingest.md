# ADR-0003: Time-Series Event Ingestion

## Context

We ingest real-time ticks, order books, and news events. These feeds need to be normalized, routed to features, stored, and replayable for backtests.

## Decision

- Ingest adapters write normalized time-series events directly onto a centralized Message Bus (`bus`).
- In-memory event bus is used for local smoke/unit tests.
- Redis Streams are used for live/paper trading to isolate ingestion processes from downstream models and agent loops.
- Time-series event payloads must include standard metadata (`timestamp`, `received_at`, `sequence_id`, `source`) to ensure backtesting determinism.

## Consequences

- Low-latency ingestion does not block agent reasoning loops.
- Backtester can swap the Redis-backed bus with an offline iterator feeding from parquet logs, ensuring online-offline equivalence.
