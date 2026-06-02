# ADR-0002: Immutable/Frozen Messages and Contracts

## Context

In high-concurrency real-time trading pipelines and deterministic backtesting, message modification causes subtle state bugs, concurrency race conditions, and telemetry mismatch.

## Decision

All event schemas (Bars, Trades, Orders, Signals, Detections) defined in `contracts` must be strictly immutable.
In Pydantic, this is enforced by setting `frozen=True` on schemas.

```python
class Bar(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
```

## Consequences

- Event handlers and downstream agents cannot accidentally mutate raw feed payloads.
- Replaying events in backtests guarantees identical inputs without copying overhead.
- Simplifies caching and hashing of tick and bar data.
