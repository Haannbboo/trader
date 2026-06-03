"""Cheap insurance: Repository must satisfy the HistoryStore Protocol.

A drift here (e.g. someone renames a method on either side) won't fail the
type checker, so we run an explicit isinstance check at test time. The
Protocol is @runtime_checkable specifically so this is possible.
"""

from __future__ import annotations

from contracts.ports import HistoryStore
from persistence.engine import Database
from persistence.repository import Repository


async def test_repository_satisfies_history_store_protocol(tmp_db: Database) -> None:
    repo = Repository(tmp_db)
    assert isinstance(repo, HistoryStore)
