from __future__ import annotations

from typing import AsyncIterator, Optional
from contracts import (
    FeatureValue,
    Instrument,
    Event,
    FeatureService as FeatureServiceInterface,
)
from feature.runtime import FeatureRuntime


class FeatureService(FeatureServiceInterface):
    """Thin facade over the FeatureRuntime; exposes derived values to the tool

    layer the SAME way market data is exposed.
    """

    def __init__(self, runtime: FeatureRuntime) -> None:
        """Initialize FeatureService with the underlying runtime."""
        self.runtime = runtime

    async def get_value(
        self,
        feature: str,
        instrument: Optional[Instrument] = None,
    ) -> FeatureValue:
        """Get the latest computed feature value for a given instrument."""
        raise NotImplementedError()

    def subscribe(self, features: list[str]) -> AsyncIterator[Event]:
        """Subscribe to specific computed feature events."""
        raise NotImplementedError()
