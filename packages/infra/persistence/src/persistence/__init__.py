"""persistence — async storage for raw facts (bars, news, fills).

Public surface:
  - Database:           async engine + session context manager.
  - PersistenceWriter:  bus consumer that durably stores BAR / NEWS / FILL events.
  - Repository:         read face (bars/news/fills) — implements HistoryStore.

Configure via a DSN (e.g. "postgresql+asyncpg://..." for prod,
"sqlite+aiosqlite:///path/to.db" for dev/test). Connection management lives in
Database; the writer and repository are stateless wrappers around it.
"""

from persistence.engine import Database
from persistence.repository import Repository
from persistence.writer import PersistenceWriter

__all__ = ["Database", "Repository", "PersistenceWriter"]
