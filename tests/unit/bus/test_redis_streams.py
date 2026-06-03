"""Unit tests for bus.redis_streams.RedisStreamBus.

Uses fakeredis to drive the bus end-to-end without a real Redis server.
fakeredis implements the redis.asyncio protocol, so the bus talks to it the
same way it would talk to a real Redis.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import List

import fakeredis
import pytest

from bus import RedisStreamBus
from contracts import (
    AssetClass,
    Bar,
    Event,
    EventType,
    Instrument,
    Quote,
    Subscription,
    Timeframe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _quote_event(symbol: str = "AAPL", source: str = "test") -> Event:
    ts = datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc)
    return Event(
        type=EventType.QUOTE,
        source=source,
        payload=Quote(
            instrument=Instrument(symbol=symbol, asset_class=AssetClass.EQUITY),
            bid=Decimal("100.00"),
            ask=Decimal("100.05"),
            ts_event=ts,
        ),
        ts_event=ts,
    )


def _bar_event(symbol: str = "AAPL", source: str = "test") -> Event:
    ts = datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc)
    return Event(
        type=EventType.BAR,
        source=source,
        payload=Bar(
            instrument=Instrument(symbol=symbol, asset_class=AssetClass.EQUITY),
            timeframe=Timeframe.M1,
            ts_open=ts,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("1000"),
        ),
        ts_event=ts,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_start_stop_cycle() -> None:
    """start() + stop() are idempotent enough to round-trip on a fresh bus."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    b = RedisStreamBus(client=client, stream="test:events")

    await b.start()
    await b.stop()

    await client.aclose()


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_publish_writes_event_to_stream() -> None:
    """publish() persists the event to the underlying Redis Stream."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    b = RedisStreamBus(client=client, stream="test:events")
    await b.start()

    event = _quote_event()
    await b.publish(event)

    entries = await client.xrange("test:events")
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    assert "data" in fields
    # The serialized JSON should at least contain the event type and source
    assert EventType.QUOTE.value in fields["data"]
    assert "test" in fields["data"]

    await b.stop()
    await client.aclose()


# ---------------------------------------------------------------------------
# subscribe — broadcast mode (no group)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_subscribe_yields_published_event() -> None:
    """subscribe() yields events published after the subscription is open."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    b = RedisStreamBus(client=client, stream="test:events")
    await b.start()

    sub = Subscription(event_types=(EventType.QUOTE,))
    event = _quote_event()

    received: List[Event] = []

    async def collect() -> None:
        async for e in b.subscribe(sub):
            received.append(e)
            if len(received) >= 1:
                return

    task = asyncio.create_task(collect())
    # Give subscribe a moment to register before publishing
    await asyncio.sleep(0.05)
    await b.publish(event)
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 1
    assert received[0].event_id == event.event_id
    assert received[0].type == EventType.QUOTE
    assert received[0].source == "test"

    await b.stop()
    await client.aclose()


@pytest.mark.asyncio
async def test_subscribe_filters_by_event_type() -> None:
    """subscribe() only yields events matching the subscription's event_types."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    b = RedisStreamBus(client=client, stream="test:events")
    await b.start()

    sub = Subscription(event_types=(EventType.QUOTE,))

    received: List[Event] = []

    async def collect() -> None:
        async for e in b.subscribe(sub):
            received.append(e)
            if len(received) >= 1:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    # Publish a BAR first (should be filtered out), then a QUOTE
    await b.publish(_bar_event())
    await b.publish(_quote_event())
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 1
    assert received[0].type == EventType.QUOTE

    await b.stop()
    await client.aclose()


@pytest.mark.asyncio
async def test_subscribe_round_trips_event_payload() -> None:
    """The yielded Event preserves its typed payload across publish/subscribe."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    b = RedisStreamBus(client=client, stream="test:events")
    await b.start()

    sub = Subscription(event_types=(EventType.QUOTE,))
    event = _quote_event()

    received: List[Event] = []

    async def collect() -> None:
        async for e in b.subscribe(sub):
            received.append(e)
            if len(received) >= 1:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    await b.publish(event)
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 1
    payload = received[0].payload
    # The deserialized payload must be a Quote with its values intact
    assert isinstance(payload, Quote)
    assert payload.bid == Decimal("100.00")
    assert payload.ask == Decimal("100.05")
    assert payload.instrument.symbol == "AAPL"

    await b.stop()
    await client.aclose()


# ---------------------------------------------------------------------------
# subscribe — consumer-group mode
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_subscribe_with_consumer_group() -> None:
    """subscribe(group=...) uses Redis consumer groups for fan-out."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    b = RedisStreamBus(client=client, stream="test:events")
    await b.start()

    sub = Subscription()
    event = _quote_event()

    received: List[Event] = []

    async def collect() -> None:
        async for e in b.subscribe(sub, group="g1"):
            received.append(e)
            if len(received) >= 1:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    await b.publish(event)
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 1
    assert received[0].event_id == event.event_id

    await b.stop()
    await client.aclose()
