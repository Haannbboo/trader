# ADR-0006: Persistence Read Face

## Context

`packages/infra/persistence/` had a working write side (Database + models +
PersistenceWriter) but no read face. The existing `Repository` in
`repository.py` was a docstring-only stub, the `HistoryStore` Protocol was
sketched next to its (missing) implementation, and the package's public
surface (`__init__.py`) still exposed a `PersistenceManager` class with
`NotImplementedError` stubs from a prior design. The persistence layer
also wasn't import-clean: `sqlalchemy`, `aiosqlite`, and `asyncpg` were
imported by the package's code but not declared in `pyproject.toml`.

## Decision

### 1. `HistoryStore` lives in `contracts.ports`

The read face is a Protocol in `packages/contracts/src/contracts/ports.py`.
Services depend on the Protocol, not on the concrete `Repository`, so
swapping the storage backend (Postgres -> SQLite for tests, or to a
column store later) is a one-class change with zero service impact.

### 2. Hand-mapped row -> DTO, not ORM relationship mapping

`Repository` uses SQLAlchemy 2.0 Core `select()` and returns
schema DTOs (frozen pydantic). The mapping is hand-written in pure
functions (`_bar_row_to_dto`, etc.) — no ORM Session relationships, no
`.to_dto()` methods on the row classes.

Rationale: the existing models are deliberately flat (a field that
will ever be filtered/sorted/grouped is its own column, per the
`models.py` docstring), so relationship-based ORM mapping buys
nothing. Frozen pydantic DTOs are not ORM targets, so hand-mapping is
unavoidable regardless.

### 3. Dialect-aware upsert in the writer

`PersistenceWriter._upsert` branches on `Database.dialect_name`:
- `"postgresql"` -> `ON CONFLICT DO NOTHING` (Postgres 9.5+)
- `"sqlite"`     -> `INSERT OR IGNORE`

Index elements come from each row type's `__table__.primary_key.columns`,
so the upsert stays in sync with the schema if PKs change. Re-publish
or replay of the same event is a no-op, not a duplicate. This is the
mechanism the read side relies on to assume row uniqueness.

### 4. `Database.create_all()` for now, not `alembic`

The package still uses `Database.create_all()` to provision the schema.
This is fine for dev / test / a single-tenant deploy. A migration
framework (alembic) is a follow-up — the project doesn't have
migrations yet, and adding it here would balloon the scope of this PR.

### 5. Deferred (out of scope for this PR)

Each of these is its own design conversation; they're listed here so the
next agent picks them up without re-discovering the gap.

- **`replay_events`.** K-way merge across bars+news+fills in `ts_event`
  global order. The backtest harness needs this. Worth deciding SQL
  (`UNION ALL` + `ORDER BY`) vs Python (heap-merge over per-table
  range reads) when picked up.
- **News instrument link table.** `NewsRow` has no instrument column;
  filtering news by `instruments=...` is intentionally not in
  `HistoryStore.fetch_news`. Add a `news_instruments` link table, then
  add the filter to the Protocol.
- **Fill `client_order_id` column.** `FillRow` has no `client_order_id`
  column; the filter is intentionally not in `HistoryStore.fetch_fills`.
  Add the column to `FillRow`, populate it from the `Fill` DTO in
  `_fill_row`, then add the filter to the Protocol.
- **Wiring `PersistenceWriter` into `apps/live/main.py`.** Add
  `PersistenceSettings` to `config.InfraSettings` (DSN, mirroring the
  `BusSettings` pattern), and a `Database` instance in the live process.
- **Bus replay wire-up.** `redis_streams.replay()` is currently a stub;
  it should read the cold window from `HistoryStore` and the warm
  window from the Redis Stream.
- **`currency` / `exchange` are not persisted.** `schema.Instrument`
  has `currency` and `exchange` fields that aren't in `_InstrumentCols`.
  The read-side mapper falls back to the pydantic defaults
  (`currency="USD"`, `exchange=None`). Fixing this requires a schema
  change to the model and a writer-side update, then a backfill.

### 6. `PersistenceError`

A new `contracts.errors.PersistenceError` exception. Used by
`Repository` for the cases where the DB is reachable but a row is
corrupt (can't be re-inflated into a DTO). Connection / pool errors
still propagate as `sqlalchemy.exc.SQLAlchemyError` so callers can
retry if they want.

## Consequences

- The read side is now usable: any service that needs historical bars,
  news, or fills takes a `HistoryStore` in its constructor and calls
  `fetch_*` — no SQLAlchemy in the consumer.
- The writer is idempotent. A bus replay that re-publishes events from
  the start of the stream no longer produces duplicate rows.
- The package has a clean public surface: `Database`,
  `PersistenceWriter`, `Repository`.
- The follow-up list above is the next chunk of work; it is NOT in
  this PR.
