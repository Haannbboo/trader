from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from contracts import (
    AssetClass,
    Instrument,
    OptionRight,
    instrument_to_occ,
    occ_to_instrument,
)


def test_occ_to_instrument_builds_option_instrument() -> None:
    instrument = occ_to_instrument("AAPL260612C00300000")

    assert instrument == Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2026, 6, 12, tzinfo=timezone.utc),
        strike=Decimal("300"),
        right=OptionRight.CALL,
    )


def test_occ_to_instrument_accepts_padded_root() -> None:
    instrument = occ_to_instrument("AAPL  260612P00300500")

    assert instrument.symbol == "AAPL"
    assert instrument.asset_class is AssetClass.OPTION
    assert instrument.expiry == datetime(2026, 6, 12, tzinfo=timezone.utc)
    assert instrument.right is OptionRight.PUT
    assert instrument.strike == Decimal("300.5")


def test_occ_to_instrument_rejects_invalid_symbol() -> None:
    with pytest.raises(ValueError, match="Invalid OCC option symbol"):
        occ_to_instrument("AAPL260612X00300000")


def test_instrument_to_occ_uses_unpadded_root() -> None:
    instrument = Instrument(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2026, 6, 12, tzinfo=timezone.utc),
        strike=Decimal("300"),
        right=OptionRight.CALL,
    )

    assert instrument_to_occ(instrument) == "AAPL260612C00300000"


def test_instrument_to_occ_handles_low_strike_and_put_right() -> None:
    instrument = Instrument(
        symbol="SPY",
        asset_class=AssetClass.OPTION,
        expiry=datetime(2024, 6, 21, tzinfo=timezone.utc),
        strike=Decimal("500"),
        right=OptionRight.PUT,
    )

    assert instrument_to_occ(instrument) == "SPY240621P00500000"
