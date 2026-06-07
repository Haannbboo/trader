from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from contracts.schema import AssetClass, Instrument, OptionRight

_OCC_TRAILER_LEN = 15
_OCC_STRIKE_SCALE = Decimal(1000)


def occ_to_instrument(value: str) -> Instrument:
    """Parse an OCC option symbol into the project Instrument schema.

    Accepts both unpadded roots used by Alpaca (``AAPL260612C00300000``) and
    padded OCC roots (``AAPL  260612C00300000``). The strike is encoded as
    strike * 1000 in the final eight digits.
    """
    raw = value.strip().upper()
    if len(raw) <= _OCC_TRAILER_LEN:
        raise ValueError(f"Invalid OCC option symbol: {value!r}")

    root = raw[:-_OCC_TRAILER_LEN].strip()
    expiry_part = raw[-_OCC_TRAILER_LEN:-9]
    right_part = raw[-9]
    strike_part = raw[-8:]

    if (
        not root
        or len(root) > 6
        or not expiry_part.isdigit()
        or right_part not in {"C", "P"}
        or not strike_part.isdigit()
    ):
        raise ValueError(f"Invalid OCC option symbol: {value!r}")

    year = 2000 + int(expiry_part[:2])
    month = int(expiry_part[2:4])
    day = int(expiry_part[4:6])
    try:
        expiry = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"Invalid OCC option symbol: {value!r}") from exc

    right = OptionRight.CALL if right_part == "C" else OptionRight.PUT
    strike = Decimal(strike_part) / _OCC_STRIKE_SCALE
    return Instrument(
        symbol=root,
        asset_class=AssetClass.OPTION,
        expiry=expiry,
        strike=strike,
        right=right,
    )


def instrument_to_occ(instrument: Instrument) -> str:
    """Format an option Instrument as the unpadded OCC symbol Alpaca expects."""
    if instrument.asset_class is not AssetClass.OPTION:
        raise ValueError(
            f"OCC option symbol requires OPTION instrument, got "
            f"{instrument.asset_class.value}"
        )
    if (
        instrument.expiry is None
        or instrument.strike is None
        or instrument.right is None
    ):
        raise ValueError(
            "Option Instrument requires expiry, strike, and right to derive "
            "the OCC symbol"
        )

    root = instrument.symbol.strip().upper()
    if not root or len(root) > 6:
        raise ValueError(f"Option root must be 1-6 characters: {instrument.symbol!r}")

    expiry = instrument.expiry.astimezone(timezone.utc)
    right = "C" if instrument.right is OptionRight.CALL else "P"
    strike_int = int((instrument.strike * _OCC_STRIKE_SCALE).to_integral_value())
    if strike_int < 0 or strike_int > 99_999_999:
        raise ValueError(f"Option strike is outside OCC range: {instrument.strike!r}")

    return f"{root}{expiry:%y%m%d}{right}{strike_int:08d}"
