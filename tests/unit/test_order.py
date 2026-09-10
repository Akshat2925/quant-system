from decimal import Decimal

import pytest

from quant_system.core import IllegalOrderTransition, Order, OrderStatus, OrderType, Side


def make_order(**overrides) -> Order:
    defaults = dict(
        client_order_id="qsabc123",
        instrument_token=1,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        limit_price=Decimal("100.00"),
        strategy_run_id="run-1",
    )
    defaults.update(overrides)
    return Order(**defaults)


def test_limit_order_requires_limit_price():
    with pytest.raises(Exception):
        Order(
            client_order_id="x",
            instrument_token=1,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=10,
            strategy_run_id="run-1",
        )


def test_sl_order_requires_trigger_price():
    with pytest.raises(Exception):
        Order(
            client_order_id="x",
            instrument_token=1,
            side=Side.BUY,
            order_type=OrderType.SL,
            quantity=10,
            limit_price=Decimal("100"),
            strategy_run_id="run-1",
        )


def test_legal_transition_sequence():
    order = make_order()
    assert order.status == OrderStatus.PENDING
    order.transition_to(OrderStatus.SUBMITTED)
    order.transition_to(OrderStatus.OPEN)
    order.transition_to(OrderStatus.FILLED)
    assert order.status == OrderStatus.FILLED


def test_illegal_transition_raises():
    order = make_order()
    with pytest.raises(IllegalOrderTransition):
        order.transition_to(OrderStatus.FILLED)  # PENDING -> FILLED is not allowed


def test_terminal_states_have_no_outgoing_transitions():
    order = make_order()
    order.transition_to(OrderStatus.SUBMITTED)
    order.transition_to(OrderStatus.REJECTED)
    with pytest.raises(IllegalOrderTransition):
        order.transition_to(OrderStatus.OPEN)


def test_unknown_can_resolve_to_any_status():
    order = make_order()
    order.transition_to(OrderStatus.SUBMITTED)
    order.transition_to(OrderStatus.UNKNOWN)  # e.g. broker call timed out
    order.transition_to(OrderStatus.FILLED)  # reconciliation resolved it
    assert order.status == OrderStatus.FILLED


def test_partial_fill_then_full_fill_weighted_average_price():
    order = make_order(quantity=10)
    order.transition_to(OrderStatus.SUBMITTED)
    order.transition_to(OrderStatus.OPEN)

    order.apply_fill(4, Decimal("100.00"))
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == 4
    assert order.average_fill_price == Decimal("100.00")
    assert order.remaining_quantity == 6

    order.apply_fill(6, Decimal("101.50"))
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == 10
    # weighted avg = (4*100 + 6*101.50) / 10 = 100.90
    assert order.average_fill_price == Decimal("100.90")
    assert order.remaining_quantity == 0


def test_fill_exceeding_remaining_quantity_raises():
    order = make_order(quantity=5)
    order.transition_to(OrderStatus.SUBMITTED)
    order.transition_to(OrderStatus.OPEN)
    with pytest.raises(ValueError):
        order.apply_fill(6, Decimal("100.00"))


def test_zero_or_negative_fill_quantity_raises():
    order = make_order(quantity=5)
    order.transition_to(OrderStatus.SUBMITTED)
    order.transition_to(OrderStatus.OPEN)
    with pytest.raises(ValueError):
        order.apply_fill(0, Decimal("100.00"))
