from typing import Dict, Any, List
from contracts import Event, Subscription, Processor
from plugins import register


@register("feature", "returns")
class ReturnsProcessor(Processor):
    """Computes simple percentage returns over configurable lag intervals."""

    def __init__(self) -> None:
        """Initialize ReturnsProcessor with configurations."""
        self.lag = 1
        self.prices: list[float] = []

    @property
    def name(self) -> str:
        """Get name of the processor."""
        return "returns"

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
        """Compute returns on incoming events."""
        raise NotImplementedError()
