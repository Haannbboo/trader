from __future__ import annotations

from typing import AsyncIterator, Optional

from contracts import (
    Event,
    FeatureValue,
    Instrument,
)
from contracts import (
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
        inst_key = instrument.key if instrument else ""
        key = (feature, inst_key)
        if key in self.runtime.latest_values:
            return self.runtime.latest_values[key]
        raise ValueError(
            f"No computed value found for feature '{feature}' and instrument '{inst_key}'"
        )

    async def subscribe(self, features: list[str]) -> AsyncIterator[Event]:
        """Subscribe to specific computed feature events."""
        from contracts import EventType, Subscription

        sub = Subscription(event_types=(EventType.FEATURE,))
        async for event in self.runtime.bus.subscribe(sub):
            if event.payload and event.payload.feature in features:
                yield event
