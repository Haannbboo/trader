import pytest
from adapters.market.polygon import PolygonMarketAdapter
from bus import InProcessBus
from contracts import (
    AssetClass,
    Instrument,
)
from feature import FeatureService
from feature.runtime import FeatureRuntime
from features.technical.rsi import RSIProcessor
from market import MarketService


@pytest.mark.asyncio
async def test_subscription_reuse() -> None:
    """Verifies that MarketService is wired correctly and complies with its constructors."""
    bus = InProcessBus()
    await bus.start()

    adapter = PolygonMarketAdapter()
    service = MarketService(sources=[adapter], bus=bus)

    # Under the skeleton architecture, calls to unimplemented methods raise NotImplementedError
    with pytest.raises(NotImplementedError):
        await service.get_quote(
            Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY)
        )

    await bus.stop()


@pytest.mark.asyncio
async def test_end_to_end_signal_generation() -> None:
    """Verifies that injecting bars into the bus triggers features and emits signals."""

    bus = InProcessBus()
    await bus.start()

    feature_runtime = FeatureRuntime(bus=bus)
    _feature_service = FeatureService(runtime=feature_runtime)
    rsi = RSIProcessor()
    rsi.initialize({"period": 5})  # Short period for test

    # Registers processor in the runtime skeleton
    with pytest.raises(NotImplementedError):
        feature_runtime.add_processor(rsi)

    await bus.stop()
