from typing import Dict, Any, List
from contracts import (
    Event, Subscription, Processor
)
from plugins import register


@register("feature", "zscore")
class CrossSectionalZScoreProcessor(Processor):
    """Computes cross-sectional z-score of returns across active symbols."""

    def __init__(self) -> None:
        """Initialize CrossSectionalZScoreProcessor with configurations."""
        self.universe: list[str] = []

    @property
    def name(self) -> str:
        """Get name of the processor."""
        return "zscore"

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
        """Compute z-score on incoming events."""
        raise NotImplementedError()
