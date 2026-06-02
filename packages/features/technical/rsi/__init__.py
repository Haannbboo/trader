from typing import Dict, Any, List
from contracts import Event, Subscription, Processor
from plugins import register


@register("feature", "rsi")
class RSIProcessor(Processor):
    """Computes Relative Strength Index (RSI) indicator from Bar events."""

    def __init__(self) -> None:
        """Initialize RSIProcessor with configurations."""
        self.period = 14
        self.prices: list[float] = []

    @property
    def name(self) -> str:
        """Get name of the processor."""
        return "rsi"

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
        """Compute RSI on incoming events."""
        raise NotImplementedError()
