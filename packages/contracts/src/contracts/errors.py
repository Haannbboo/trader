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
