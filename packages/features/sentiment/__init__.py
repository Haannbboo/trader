from typing import Any, Dict, List

from contracts import Event, Processor, Subscription
from plugins import register


@register("feature", "sentiment")
class SentimentProcessor(Processor):
    """Computes sentiment score for news headlines using LLM/BERT, falling back to heuristics if deps are missing."""

    def __init__(self) -> None:
        """Initialize SentimentProcessor with configurations."""
        self.model_name = ""

    @property
    def name(self) -> str:
        """Get name of the processor."""
        return "sentiment"

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
        """Compute sentiment score on incoming news events."""
        raise NotImplementedError()
