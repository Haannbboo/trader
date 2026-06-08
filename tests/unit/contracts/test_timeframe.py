"""Tests for Timeframe.interval."""

from __future__ import annotations

from datetime import timedelta

from contracts.schema import Timeframe


def test_timeframe_interval_s1() -> None:
    assert Timeframe.S1.interval == timedelta(seconds=1)


def test_timeframe_interval_m1() -> None:
    assert Timeframe.M1.interval == timedelta(minutes=1)


def test_timeframe_interval_m5() -> None:
    assert Timeframe.M5.interval == timedelta(minutes=5)


def test_timeframe_interval_m15() -> None:
    assert Timeframe.M15.interval == timedelta(minutes=15)


def test_timeframe_interval_h1() -> None:
    assert Timeframe.H1.interval == timedelta(hours=1)


def test_timeframe_interval_d1() -> None:
    assert Timeframe.D1.interval == timedelta(days=1)
