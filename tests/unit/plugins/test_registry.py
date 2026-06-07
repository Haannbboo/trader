from contracts import SourceCapabilities, SourceMode, SourcePort
from plugins import Registry, SourceConfig


class DemoSource:
    def __init__(self, *, token: str) -> None:
        self.name = "demo"
        self.token = token

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            mode=SourceMode.POLL,
            supports_streaming=False,
            asset_classes=(),
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> bool:
        return True


def test_build_sources_uses_explicit_source_and_name() -> None:
    registry = Registry()
    registry.register("market", "demo", "stock")(DemoSource)

    sources = registry.build_sources(
        "market",
        [SourceConfig(source="demo", name="stock", params={"token": "abc"})],
        as_=SourcePort,
    )

    assert len(sources) == 1
    assert isinstance(sources[0], DemoSource)
    assert sources[0].token == "abc"
