"""Position P&L reconciliation tests.

These are the tests that stand in for "matches to the paisa" — every
scenario here has a hand-computed expected value in the comment above the
assertion. If a change to Position.apply_fill breaks one of these, that is
exactly the class of bug the instructions call out: a regression test
should exist for every strategy-affecting change, and this file is where
position-level ones live.
"""

from decimal import Decimal

import pytest

from quant_system.core import Fill, Position, Side


def make_fill(side: Side, qty: int, price: str, costs: str = "0", fill_id: str = "f1") -> Fill:
    return Fill(
        fill_id=fill_id,
        client_order_id="co1",
        instrument_token=1,
        side=side,
        quantity=qty,
        price=Decimal(price),
        brokerage=Decimal(costs),
        source="backtest",
    )


def test_opening_a_long_position():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.BUY, 10, "100.00"))
    assert pos.quantity == 10
    assert pos.average_price == Decimal("100.00")
    assert pos.realized_pnl == Decimal("0")
    assert pos.pyramid_level == 0


def test_pyramiding_long_updates_weighted_average():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.BUY, 10, "100.00", fill_id="f1"))
    pos.apply_fill(make_fill(Side.BUY, 10, "110.00", fill_id="f2"))
    # (10*100 + 10*110) / 20 = 105.00
    assert pos.quantity == 20
    assert pos.average_price == Decimal("105.00")
    # pyramid_level counts adds since flat: the opening fill is level 0,
    # this second same-direction fill is the first "add" -> level 1.
    assert pos.pyramid_level == 1


def test_partial_close_of_long_realizes_pnl_on_closed_portion_only():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.BUY, 20, "100.00", fill_id="f1"))
    pos.apply_fill(make_fill(Side.SELL, 8, "110.00", fill_id="f2"))
    # realized = (110 - 100) * 8 = 80
    assert pos.realized_pnl == Decimal("80")
    assert pos.quantity == 12
    # average price on the remaining long leg is unchanged by an exit fill
    assert pos.average_price == Decimal("100.00")


def test_full_close_of_long_flattens_position():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.BUY, 10, "100.00", fill_id="f1"))
    pos.apply_fill(make_fill(Side.SELL, 10, "95.00", fill_id="f2"))
    # realized = (95 - 100) * 10 = -50
    assert pos.realized_pnl == Decimal("-50")
    assert pos.is_flat
    assert pos.average_price == Decimal("0")
    assert pos.pyramid_level == 0


def test_reversal_from_long_to_short_realizes_and_reopens():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.BUY, 10, "100.00", fill_id="f1"))
    # Sell 15: closes the 10 long (realize (90-100)*10 = -100) and opens
    # a fresh 5-lot short at 90.
    pos.apply_fill(make_fill(Side.SELL, 15, "90.00", fill_id="f2"))
    assert pos.realized_pnl == Decimal("-100")
    assert pos.quantity == -5
    assert pos.average_price == Decimal("90.00")
    assert pos.is_short
    assert pos.pyramid_level == 0


def test_short_side_is_symmetric():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.SELL, 10, "100.00", fill_id="f1"))
    assert pos.quantity == -10
    assert pos.average_price == Decimal("100.00")

    pos.apply_fill(make_fill(Side.BUY, 4, "90.00", fill_id="f2"))
    # closing a short: realized = (100 - 90) * 4 = 40  (profit on a short covered cheaper)
    assert pos.realized_pnl == Decimal("40")
    assert pos.quantity == -6
    assert pos.average_price == Decimal("100.00")


def test_pyramiding_short_updates_weighted_average():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.SELL, 10, "100.00", fill_id="f1"))
    pos.apply_fill(make_fill(Side.SELL, 10, "90.00", fill_id="f2"))
    # (10*100 + 10*90) / 20 = 95.00
    assert pos.quantity == -20
    assert pos.average_price == Decimal("95.00")


def test_unrealized_pnl_long():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.BUY, 10, "100.00"))
    assert pos.unrealized_pnl(Decimal("105.00")) == Decimal("50")
    assert pos.unrealized_pnl(Decimal("95.00")) == Decimal("-50")


def test_unrealized_pnl_short():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.SELL, 10, "100.00"))
    assert pos.unrealized_pnl(Decimal("95.00")) == Decimal("50")
    assert pos.unrealized_pnl(Decimal("105.00")) == Decimal("-50")


def test_unrealized_pnl_when_flat_is_zero():
    pos = Position(instrument_token=1)
    assert pos.unrealized_pnl(Decimal("999")) == Decimal("0")


def test_costs_accumulate_across_fills():
    pos = Position(instrument_token=1)
    pos.apply_fill(make_fill(Side.BUY, 10, "100.00", costs="12.50", fill_id="f1"))
    pos.apply_fill(make_fill(Side.SELL, 10, "105.00", costs="13.10", fill_id="f2"))
    assert pos.total_costs == Decimal("25.60")


def test_full_reconciliation_scenario_grid_style_sequence():
    """A representative grid sequence: build up in three tranches, then
    exit in two tranches, then reverse into a short. Every number here is
    hand-computed — this is the shape of test the 'reconciles to the
    paisa' acceptance criterion demands for the real backtest harness."""
    pos = Position(instrument_token=1)

    pos.apply_fill(make_fill(Side.BUY, 5, "100.00", fill_id="f1"))
    pos.apply_fill(make_fill(Side.BUY, 5, "98.00", fill_id="f2"))
    pos.apply_fill(make_fill(Side.BUY, 5, "96.00", fill_id="f3"))
    # avg = (5*100 + 5*98 + 5*96) / 15 = 98.00
    assert pos.quantity == 15
    assert pos.average_price == Decimal("98.00")

    pos.apply_fill(make_fill(Side.SELL, 7, "101.00", fill_id="f4"))
    # realized = (101 - 98) * 7 = 21
    assert pos.realized_pnl == Decimal("21")
    assert pos.quantity == 8
    assert pos.average_price == Decimal("98.00")  # unchanged by exit

    pos.apply_fill(make_fill(Side.SELL, 12, "94.00", fill_id="f5"))
    # closes remaining 8: realized += (94 - 98) * 8 = -32  -> total 21 - 32 = -11
    # opens short 4 @ 94
    assert pos.realized_pnl == Decimal("-11")
    assert pos.quantity == -4
    assert pos.average_price == Decimal("94.00")
    assert pos.is_short
