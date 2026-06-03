from __future__ import annotations

from typing import Dict

from contracts.ports import Bus, Processor


class FeatureRuntime:
    """Manages the DAG of feature processors, historical warmups, and execution loops."""

    def __init__(self, bus: Bus) -> None:
        """Initialize FeatureRuntime with the event bus."""
        self.bus = bus
        self.processors: Dict[str, Processor] = {}

    def add_processor(self, processor: Processor) -> None:
        """Register a feature processor and wire it into the DAG."""
        raise NotImplementedError()

    async def start(self) -> None:
        """Start the processing loop consuming from the bus and routing to processors."""
        raise NotImplementedError()

    async def stop(self) -> None:
        """Stop the runtime loop."""
        raise NotImplementedError()
