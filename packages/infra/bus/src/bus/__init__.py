"""
bus — two implementations of the Bus protocol.

`InProcessBus` is the default (no extra deps; single-process asyncio fan-out).
`RedisStreamBus` is the durable, multi-process backend — it lives behind the
`redis` optional extra (`uv pip install -e .[redis]`) because importing it
requires the `redis` package, which we don't want to force on InProcessBus
users.

Both are exposed as `from bus import <Name>` for callers, but the redis
re-export is lazy: if the `redis` extra isn't installed, importing InProcessBus
works fine and a delayed access of `RedisStreamBus` raises a clear ImportError
with install instructions (rather than a top-level ModuleNotFoundError at
import time).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts.ports import Bus

from .inprocess import InProcessBus

if TYPE_CHECKING:
    # Mypy / type-checker only — keeps `from bus import RedisStreamBus`
    # typed without forcing the runtime import.
    from .redis_streams import RedisStreamBus

__all__ = ["InProcessBus", "RedisStreamBus", "Bus"]


def __getattr__(name: str):
    """Lazy attribute access so the `redis` package is only required when
    `RedisStreamBus` is actually requested (constructed, not just imported)."""
    if name == "RedisStreamBus":
        try:
            from .redis_streams import RedisStreamBus
        except ModuleNotFoundError as exc:
            if exc.name in ("redis", "redis.asyncio"):
                raise ImportError(
                    "RedisStreamBus requires the `redis` package. "
                    "Install with `uv pip install -e .[redis]`."
                ) from exc
            raise
        # Cache on the module so subsequent accesses don't re-import.
        globals()["RedisStreamBus"] = RedisStreamBus
        return RedisStreamBus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
