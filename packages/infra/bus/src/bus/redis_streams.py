"""
ta.bus.redis_streams — Bus backed by Redis Streams for durable, multi-process
fan-out with consumer-group semantics. Drop-in for InProcessBus; same Bus
protocol, same Subscription filter, so callers don't change when they swap
implementations.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Optional, cast

import redis.asyncio
from contracts.ports import HistoryStore, Subscription
from contracts.schema import (
    Balance,
    Bar,
    Event,
    EventType,
    FeatureValue,
    Fill,
    NewsItem,
    Order,
    Position,
    Quote,
)
from loguru import logger
from redis.exceptions import ResponseError

from ._filters import matches_subscription

# Map EventType -> payload class. Events are deserialized into the right typed
# payload here, because Pydantic's Generic[PayloadT] carries no runtime type
# information once an event is round-tripped through JSON.
_PAYLOAD_TYPE_FOR_EVENT = {
    EventType.QUOTE: Quote,
    EventType.BAR: Bar,
    EventType.NEWS: NewsItem,
    EventType.ORDER_UPDATE: Order,
    EventType.FILL: Fill,
    EventType.POSITION_UPDATE: Position,
    EventType.BALANCE_UPDATE: Balance,
    EventType.FEATURE: FeatureValue,
}

# How long subscribe's XREAD/XREADGROUP blocks per loop iteration. Short so
# tests can stop iterating promptly; production callers can still keep up at
# any sane event rate.
_BLOCK_MS = 100
# Maximum events pulled per XREAD. Keeps the per-loop work bounded.
_BATCH = 100


class RedisStreamBus:
    """Asynchronous event bus backed by Redis Streams.

    - `publish(event)`  → XADD onto a single stream
    - `subscribe(...)`  → AsyncIterator[Event] that filters on the fly
        - with `group=...` → uses XREADGROUP (consumer-group fan-out / replay)
        - without a group  → uses XREAD (broadcast; each consumer sees every msg)
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        *,
        client: Optional[Any] = None,
        stream: str = "trader:events",
        maxlen: Optional[int] = 100_000,
    ) -> None:
        if redis_url is None and client is None:
            raise ValueError("redis_url or client is required")
        if redis_url is not None and client is not None:
            raise ValueError("pass redis_url or client, not both")
        self.redis_url = redis_url
        self._client: redis.asyncio.Redis | None = client
        self._stream = stream
        self._maxlen = maxlen
        self._owned_client = client is None  # True means we should aclose on stop

    async def start(self) -> None:
        if self._client is None:
            # decode_responses=True so XREAD/XRANGE fields come back as str
            # instead of bytes — keeps the deserialize path and any downstream
            # consumers from having to think about encoding.
            assert (
                self.redis_url is not None
            ), "redis_url is required when client is not pre-provided"
            self._client = redis.asyncio.from_url(self.redis_url, decode_responses=True)
            self._owned_client = True
        logger.info(f"RedisStreamBus started (stream={self._stream}).")

    async def stop(self) -> None:
        if self._client is not None and self._owned_client:
            await self._client.aclose()
        self._client = None
        logger.info("RedisStreamBus stopped.")

    async def publish(self, event: Event) -> None:
        if self._client is None:
            raise RuntimeError("RedisStreamBus is not started")
        data = event.model_dump_json()
        kwargs: dict = {"data": data}
        if self._maxlen is not None:
            # `approximate=True` lets Redis trim in cheap ~N batches instead of
            # exact per-entry trimming — fine for an event bus, the cap is a
            # backpressure bound, not a hard ledger.
            await self._client.xadd(
                self._stream, kwargs, maxlen=self._maxlen, approximate=True
            )
        else:
            await self._client.xadd(self._stream, kwargs)

    def subscribe(
        self,
        subscription: Subscription,
        *,
        group: Optional[str] = None,
    ) -> AsyncIterator[Event]:
        if self._client is None:
            raise RuntimeError("RedisStreamBus is not started")
        return self._subscribe(subscription, group)

    async def replay(
        self,
        subscription: Subscription,
        start: datetime,
        end: datetime,
        *,
        history: HistoryStore,
    ) -> AsyncIterator[Event]:
        """Replay historical events matching `subscription` in [start, end).

        STUB: not implemented yet. The InProcessBus implementation reads
        from a SQL-backed HistoryStore; this implementation will read from
        a Redis stream range scan instead. `history` is accepted for
        protocol-shape uniformity and is ignored here.
        """
        raise NotImplementedError
        if False:
            yield  # make this an async generator so AsyncIterator is honest

    async def _subscribe(
        self,
        subscription: Subscription,
        group: Optional[str],
    ) -> AsyncIterator[Event]:
        """Inner generator. Sets up the consumer group once, then loops on
        XREAD/XREADGROUP, deserializing + filtering entries and yielding the
        ones that match `subscription`."""
        assert self._client is not None, "RedisStreamBus is not started"
        client = self._client
        stream = self._stream

        # Set up the consumer group, if asked. BUSYGROUP is fine — it just means
        # the group already exists from a previous run, which is the normal case
        # for a long-lived durable bus.
        consumer: Optional[str] = None
        if group is not None:
            consumer = f"{group}-{uuid.uuid4().hex[:8]}"
            try:
                await client.xgroup_create(stream, group, id="0", mkstream=True)
            except ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

        # For broadcast mode, track the last entry-id we've yielded so we don't
        # re-deliver. Start at "0" so a fresh subscriber gets history.
        last_id = "0"

        while True:
            try:
                if group is not None:
                    assert consumer is not None
                    raw_msgs = await client.xreadgroup(
                        group, consumer, {stream: ">"}, count=_BATCH, block=_BLOCK_MS
                    )
                else:
                    raw_msgs = await client.xread(
                        {stream: last_id}, count=_BATCH, block=_BLOCK_MS
                    )
                # Cast to list[tuple] to override redis-py's broad XReadResponse type (which
                # includes dict shapes that trigger Pyrefly dict-key unpacking errors).
                msgs = cast("list[tuple[str, list[tuple[str, dict]]]]", raw_msgs)
            except Exception:
                logger.exception("RedisStreamBus subscribe: xread failed; aborting")
                return

            delivered_any = False
            for _stream_name, entries in msgs:
                for entry_id, fields in entries:
                    delivered_any = True
                    if group is None:
                        # Advance our read position past this entry. In group
                        # mode Redis tracks this for us via the consumer's PEL.
                        last_id = _decode_str(entry_id)
                    raw = _field(fields, "data")
                    if raw is None:
                        continue
                    try:
                        event = _deserialize_event(raw)
                    except Exception:
                        logger.exception(
                            f"RedisStreamBus: failed to deserialize entry {entry_id}; skipping"
                        )
                        continue
                    if not matches_subscription(event, subscription):
                        continue
                    yield event

            if not delivered_any:
                # fakeredis (and some misbehaving proxies) return [] from
                # XREAD/XREADGROUP immediately even when block>0 — a real Redis
                # would have blocked for up to block_ms. Yield to the event
                # loop on a tight cadence so publishers can get scheduled.
                await asyncio.sleep(_BLOCK_MS / 1000.0)
                continue

    # ------------------------------------------------------------------
    # Subscription filter — mirrors InProcessBus._matches so the two
    # implementations are interchangeable from the caller's point of view.
    # ------------------------------------------------------------------
    @staticmethod
    def _matches(event: Event, sub: Subscription) -> bool:
        return matches_subscription(event, sub)


def _deserialize_event(raw: Any) -> Event:
    """Decode a stream entry's `data` field back into a typed Event.

    The payload is re-hydrated against the class registered for the event's
    `type` field, so `event.payload` comes back as a Quote/Bar/... instead of
    an opaque dict.
    """
    raw = _decode_str(raw)
    data = json.loads(raw)
    event_type = EventType(data["type"])
    payload_cls = _PAYLOAD_TYPE_FOR_EVENT.get(event_type)
    payload_field = data.get("payload")
    if payload_cls is not None and isinstance(payload_field, dict):
        data["payload"] = payload_cls.model_validate(payload_field)  # type: ignore[attr-defined]
    return Event.model_validate(data)


def _decode_str(value: Any) -> Any:
    """Coerce a possibly-bytes value to str. Pass-through for everything else."""
    """Coerce a possibly-bytes value to str. Pass-through for everything else."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _field(fields: dict, key: str) -> Any:
    """Look up `key` in a field dict that may be bytes- or str-keyed.

    fakeredis and real redis-py disagree about whether XREAD honors
    `decode_responses=True` on stream fields, so we tolerate both."""
    """Look up `key` in a field dict that may be bytes- or str-keyed.

    fakeredis and real redis-py disagree about whether XREAD honors
    `decode_responses=True` on stream fields, so we tolerate both.
    """
    if key in fields:
        return fields[key]
    bkey = key.encode("utf-8")
    if bkey in fields:
        return fields[bkey]
    return None
