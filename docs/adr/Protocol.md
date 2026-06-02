# ADR-0004: Protocol-based Contract Enforcement

## Context

Adapters and plugins need a shared contract interface without importing implementation details or hard subclass trees that coupling-prone developers might misuse.

## Decision

We use Python PEP 544 structural subtyping (`typing.Protocol`) in `contracts` to specify interfaces:

### Source Ports (implemented by adapters)
- `MarketSourcePort`
- `NewsSourcePort`
- `AccountSourcePort`

### Aggregated Services (implemented by S-* packages)
- `MarketDataService`
- `NewsService`
- `AccountService`
- `FeatureService`

### Event Bus & Processing Channels
- `Bus` (implemented implicitly by `InProcessBus` and `RedisStreamBus`)
- `Processor` (implemented by individual feature processors)

Adapters and services implement the required methods without strictly subclassing from concrete classes, though we provide `BaseAdapter` in `adapters/_base` for convenient boilerplate sharing.

## Consequences

- No compile-time coupling between adapters and services.
- Clean mock implementations can be easily substituted in unit testing.
- Static analysis checks (`mypy`) still guarantee compliance without class hierarchy inheritance.
