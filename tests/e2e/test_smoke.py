import anyio
import pytest
from apps.smoke.main import main as smoke_main


@pytest.mark.asyncio
async def test_smoke_slice_runs() -> None:
    """End-to-End test running the complete vertical slice to verify wiring and runtime."""
    # We run the actual smoke main, but wrap it in a timeout so tests don't hang
    with anyio.fail_after(12.0):
        await smoke_main()
