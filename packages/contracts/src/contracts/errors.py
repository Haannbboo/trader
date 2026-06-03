class TraderError(Exception):
    """Base exception class for all errors in the trader system."""

    pass


class ConnectionError(TraderError):
    """Raised when an adapter or service fails to connect to its remote peer/feed."""

    pass


class OrderRejectedError(TraderError):
    """Raised when an order is submitted but rejected by the exchange/broker."""

    pass


class InsufficientFundsError(TraderError):
    """Raised when trying to submit an order that exceeds available buying power."""

    pass


class ConfigurationError(TraderError):
    """Raised when system configurations are invalid or missing required keys."""

    pass


class GuardrailTriggeredError(TraderError):
    """Raised when an order or action is blocked by the active safety guardrails."""

    pass


class PersistenceError(TraderError):
    """Raised by the persistence layer for unrecoverable data-shape problems
    (e.g. a stored row that cannot be re-inflated back into a schema DTO).

    Connection / pool errors from SQLAlchemy propagate natively; this class is
    for the cases where the DB is reachable but a row is corrupt or the schema
    is out of sync with the schema DTOs. Services should not import
    sqlalchemy.exc — they catch PersistenceError, not the underlying driver
    errors, so swapping the storage backend doesn't touch the consumers."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
