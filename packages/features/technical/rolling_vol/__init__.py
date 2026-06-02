from typing import Dict, Any, List
from contracts import (
    Event, Subscription, Processor
)
from plugins import register


@register("feature", "rolling_vol")
class RollingVolProcessor(Processor):
    """Computes standard deviation (volatility) of returns over a rolling window."""

    def __init__(self) -> None:
        """Initialize RollingVolProcessor with configurations."""
        self.window = 20
        self.prices: list[float] = []

    @property
    def name(self) -> str:
        """Get name of the processor."""
        return "rolling_vol"

    @property
    def input(self) -> Subscription:
        """Get input subscription filter."""
        raise NotImplementedError()

    @property
    def warmup_events(self) -> int:
        """Number of warmup events required."""
        raise NotImplementedError()

    def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize processor with parameters."""
        raise NotImplementedError()

    async def on_event(self, event: Event) -> List[Event]:
        """Compute rolling volatility on incoming events."""
        raise NotImplementedError()
