# ADR-0001: Project Layout and Packages Architecture

## Context

We need an extensible, robust, and clean repository structure for a modular, agentic trading platform. The codebase must handle real-time trading pipelines, backtesting engines, and LLM agent loops, while remaining decoupling-friendly and maintaining high testability.

## Decision

We adopt a "layered workspace-like" repository structure:
- **`packages/`** contains core business logic divided into contracts, plugin registry, common transport/bus utils, adapters, domain services, features, and agent loop.
- **`apps/`** acts as a thin wiring layer containing application entry points.
- **`tests/`** houses high-level integration, e2e, and conformance tests.
- **`config/`** separates setup variables (what universe/adapters to load) from core logic.

## Consequences

- No adapter dependencies leak into the core trading engine.
- Adapters and feature processors register dynamically with registries (`plugins`), resolving dependency cycles.
- Developers can write standalone, decoupled packages that can easily graduate to standalone Python packages in a `uv workspace` layout.
