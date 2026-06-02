import json
from pathlib import Path
from typing import Any, Dict
from contracts.schema import Order, Fill


class PersistenceManager:
    """Manages writing fills, historical bars, and order execution logs to disk."""

    def __init__(self, data_dir: str = "data") -> None:
        """Initialize PersistenceManager to write to path."""
        raise NotImplementedError()

    def log_order(self, order: Order) -> None:
        """Appends order state update to a JSON lines file."""
        raise NotImplementedError()

    def log_fill(self, fill: Fill) -> None:
        """Appends executed fills to local fill logs."""
        raise NotImplementedError()

    def save_state(self, key: str, state: Dict[str, Any]) -> None:
        """Persists arbitrary state to a file."""
        raise NotImplementedError()
