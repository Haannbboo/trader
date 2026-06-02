# ADR-0005: Decoupled Plugin Registry Core

## Context

Adding a new adapter (e.g., a new exchange or news source) or a new feature factor (e.g., a custom transformer) should never require modifying the core runner code or hardcoding mappings.

## Decision

We introduce a global registry mechanism in `plugins`.
- Adapters and feature processors use decorators to self-register under standard namespaces (e.g. `@register("market", "polygon")`).
- The application bootstrapper (`apps/live/main.py` or `apps/backtest/main.py`) reads a configuration YAML and dynamically imports/instantiates the registered plugins.
- Heavy ML dependency processors (e.g. sentiment analyzers needing `torch`) are registered dynamically, preventing `torch` import overhead if the configuration does not enable them.

## Consequences

- Highly decoupled structure; adding a new broker/feed is as simple as dropping a directory under `adapters/` and registering it.
- Facilitates conformance testing: a single parameterized test can loop over all registered plugins and verify conformance.
