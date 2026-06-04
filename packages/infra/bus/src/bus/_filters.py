from __future__ import annotations

from typing import Optional

from contracts.ports import Subscription
from contracts.schema import Event, Instrument


def matches_subscription(event: Event, sub: Subscription) -> bool:
    """True iff `event` passes the subscription filter.

    Shared logic for all Bus implementations.
    """
    if sub.event_types and event.type not in sub.event_types:
        return False
    if sub.sources and event.source not in sub.sources:
        return False
    if sub.instruments:
        payload = getattr(event, "payload", None)
        event_inst_key: Optional[str] = None
        if payload is not None:
            inst = getattr(payload, "instrument", None)
            if isinstance(inst, Instrument):
                event_inst_key = inst.key
        if event_inst_key is None:
            return False
        sub_keys = {inst.key for inst in sub.instruments}
        if event_inst_key not in sub_keys:
            return False
    return True
