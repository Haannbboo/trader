from __future__ import annotations

from decimal import Decimal

import pytest
from contracts import (
    AssetClass,
    Instrument,
    Order,
    OrderType,
    Side,
    TimeInForce,
)
from guardrail import (
    BuyingPowerRule,
    Guardrail,
    MaxQuantityRule,
    RiskContext,
    RiskRejected,
    RuleResult,
)


def test_max_quantity_rule():
    rule = MaxQuantityRule(max_qty=Decimal("100"))
    ctx = RiskContext()

    # Under limit
    order_ok = Order(
        client_order_id="1",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("50"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150"),
        tif=TimeInForce.DAY,
    )
    res = rule.evaluate(order_ok, ctx)
    assert res.approved is True
    assert res.clamped_order is None

    # Over limit
    order_too_large = Order(
        client_order_id="2",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("150"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150"),
        tif=TimeInForce.DAY,
    )
    res2 = rule.evaluate(order_too_large, ctx)
    assert res2.approved is True
    assert res2.clamped_order is not None
    assert res2.clamped_order.quantity == Decimal("100")


def test_buying_power_rule():
    rule = BuyingPowerRule()
    ctx = RiskContext(buying_power=Decimal("1000"))

    # Buy under buying power
    order_buy_ok = Order(
        client_order_id="1",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("5"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150"),
        tif=TimeInForce.DAY,
    )
    res = rule.evaluate(order_buy_ok, ctx)
    assert res.approved is True

    # Buy over buying power
    order_buy_fail = Order(
        client_order_id="2",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150"),
        tif=TimeInForce.DAY,
    )
    res2 = rule.evaluate(order_buy_fail, ctx)
    assert res2.approved is False
    assert res2.reason == "insufficient buying power"

    # Sell (ignored by buying power limit)
    order_sell = Order(
        client_order_id="3",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.SELL,
        quantity=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("150"),
        tif=TimeInForce.DAY,
    )
    res3 = rule.evaluate(order_sell, ctx)
    assert res3.approved is True

    # Market order (no limit_price, ignored)
    order_market = Order(
        client_order_id="4",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.MARKET,
        limit_price=None,
        tif=TimeInForce.DAY,
    )
    res4 = rule.evaluate(order_market, ctx)
    assert res4.approved is True


@pytest.mark.asyncio
async def test_guardrail_check_flow():
    rules = [
        MaxQuantityRule(max_qty=Decimal("100")),
        BuyingPowerRule(),
    ]
    guardrail = Guardrail(rules)
    ctx = RiskContext(buying_power=Decimal("1000"))

    # Test clamping + approval
    order = Order(
        client_order_id="1",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("150"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("8"),
        tif=TimeInForce.DAY,
    )

    # 150 gets clamped to 100. 100 * 8 = 800 < 1000 buying power -> approved
    approved_order = await guardrail.check(order, ctx)
    assert approved_order.quantity == Decimal("100")

    # Test rejection
    order_expensive = Order(
        client_order_id="2",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("150"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("15"),
        tif=TimeInForce.DAY,
    )
    # 150 clamped to 100. 100 * 15 = 1500 > 1000 buying power -> rejected
    with pytest.raises(RiskRejected) as excinfo:
        await guardrail.check(order_expensive, ctx)
    assert excinfo.value.reason == "insufficient buying power"
    assert excinfo.value.rule == "buying_power"


@pytest.mark.asyncio
async def test_guardrail_kill_switch():
    rules = []
    guardrail = Guardrail(rules)
    ctx = RiskContext()

    order = Order(
        client_order_id="1",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        tif=TimeInForce.DAY,
    )

    # Check normal operation
    res = await guardrail.check(order, ctx)
    assert res == order
    assert not guardrail.tripped

    # Trip
    guardrail.trip("operator manual halt")
    assert guardrail.tripped

    with pytest.raises(RiskRejected) as excinfo:
        await guardrail.check(order, ctx)
    assert "kill switch tripped" in excinfo.value.reason
    assert excinfo.value.rule == "kill_switch"

    # Reset
    guardrail.reset()
    assert not guardrail.tripped
    res2 = await guardrail.check(order, ctx)
    assert res2 == order


@pytest.mark.asyncio
async def test_rule_requests_kill():
    class EmergencyRule:
        name = "emergency_rule"

        def evaluate(self, order: Order, ctx: RiskContext) -> RuleResult:
            return RuleResult(
                approved=False, reason="Emergency halt requested!", request_kill=True
            )

    guardrail = Guardrail([EmergencyRule()])
    ctx = RiskContext()

    order = Order(
        client_order_id="1",
        instrument=Instrument(symbol="AAPL", asset_class=AssetClass.EQUITY),
        side=Side.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100"),
        tif=TimeInForce.DAY,
    )

    with pytest.raises(RiskRejected) as excinfo:
        await guardrail.check(order, ctx)
    assert excinfo.value.reason == "Emergency halt requested!"
    assert excinfo.value.rule == "emergency_rule"
    assert guardrail.tripped
