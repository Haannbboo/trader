from typing import Any, Dict

from guardrail import Guardrail
from contracts import Bus


class TraderAgentHarness:
    """Core Agent Loop that listens to technical/sentiment signals and acts using LLM policies."""

    def __init__(
        self,
        bus: Bus,
        tools: Any,
        guardrail: Guardrail,
        strategy_config: Dict[str, Any],
    ) -> None:
        """Initialize Agent loop with bus, execution tools, guardrail, and strategies config."""
        self.bus = bus
        self.tools = tools
        self.guardrail = guardrail
        self.config = strategy_config

    async def start(self) -> None:
        """Start agent listening for signal/feature updates."""
        raise NotImplementedError()

    async def stop(self) -> None:
        """Stop agent loop."""
        raise NotImplementedError()
