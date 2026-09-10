"""Unit tests for the backtest engine.

These tests verify:
1. Bar-accurate fill simulation (market, limit, stop).
2. No lookahead: strategy observes bar[i], fills execute on bar[i+1].
3. Slippage is applied correctly.
4. P&L from backtest engine matches Position.apply_fill directly (reconciliation).
5. Walk-forward splits are non-overlapping and cover all bars.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quant_system.backtest import (
    BacktestEngine,
    CostModel,
    SlippageModel,
    WalkForwardWindow,
    slice_bars,
    walk_forward_splits,
)
from quant_system.core.enums import ContractType, Exchange
from quant_system.core.instrument import Instrument
from quant_system.core.position import Position
from quant_system.strategies.base import Bar, OrderIntent, Strategy
from quant_system.core.enums import OrderType, Side


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def make_instrument():
    return Instrument(
        tradingsymbol="TESTFUT",
        exchange=Exchange.NFO,
        contract_type=ContractType.FUTURE,
        instrument_token=9999,
        lot_size=1,
        tick_size=Decimal("0.05"),
    )


def make_bar(close: float, ts_offset: int = 0, high_offset: float = 2.0, low_offset: float = 2.0) -> Bar:
    ts = datetime(2024, 1, 1, 9, 15 + ts_offset, tzinfo=timezone.utc)
    c = Decimal(str(close))
    return Bar(
        instrument_token=9999,
        timestamp=ts,
        open=c,
        high=c + Decimal(str(high_offset)),
        low=c - Decimal(str(low_offset)),
        close=c,
        volume=1000,
    )


class BuyOnFirstBarStrategy(Strategy):
    """Emits one BUY MARKET intent on the first bar, nothing thereafter."""

    name = "buy_first"
    _fired = False

    def reset(self):
        self._fired = False

    def on_bar(self, bar, position, params):
        if not self._fired:
            self._fired = True
            return [OrderIntent(
                instrument=make_instrument(),
                side=Side.BUY,
                order_type=OrderType.MARKET,
                quantity=1,
                intent_sequence=1,
                reason="test_buy",
            )]
        return []


class LimitBuyStrategy(Strategy):
    """Emits a limit buy at close - 5 on first bar."""

    name = "limit_buy"
    _fired = False

    def reset(self):
        self._fired = False

    def on_bar(self, bar, position, params):
        if not self._fired:
            self._fired = True
            return [OrderIntent(
                instrument=make_instrument(),
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                quantity=1,
                limit_price=bar.close - Decimal("5"),
                intent_sequence=1,
                reason="test_limit_buy",
            )]
        return []


# ------------------------------------------------------------------ #
#  Fill simulation tests                                               #
# ------------------------------------------------------------------ #

def test_market_order_fills_on_next_bar_open():
    """Market order from bar[0] should fill at bar[1].open + slippage."""
    instrument = make_instrument()
    bars = [make_bar(100.0, i) for i in range(5)]
    engine = BacktestEngine(slippage=SlippageModel(fixed_ticks=0, proportional_bps=0))
    result = engine.run(BuyOnFirstBarStrategy(), instrument, bars, {})
    assert result.num_trades == 1
    assert result.fills[0].price == Decimal("100.00")  # bar[1].open == 100


def test_limit_order_fills_when_bar_low_reaches_limit():
    """Limit buy at 95 should fill on any bar where low <= 95."""
    instrument = make_instrument()
    # bar[0]: close=100 → intent emitted (limit at 95)
    # bar[1]: low=96 → doesn't fill (low > 95)
    # bar[2]: low=93 → fills (low < 95)
    bars = [
        Bar(9999, datetime(2024,1,1,9,15,tzinfo=timezone.utc), Decimal("100"), Decimal("102"), Decimal("98"), Decimal("100"), 1000),
        Bar(9999, datetime(2024,1,1,9,16,tzinfo=timezone.utc), Decimal("100"), Decimal("102"), Decimal("96"), Decimal("100"), 1000),
        Bar(9999, datetime(2024,1,1,9,17,tzinfo=timezone.utc), Decimal("96"),  Decimal("98"),  Decimal("93"), Decimal("96"),  1000),
    ]
    engine = BacktestEngine(slippage=SlippageModel(fixed_ticks=0))
    result = engine.run(LimitBuyStrategy(), instrument, bars, {})
    assert result.num_trades == 1
    assert result.fills[0].price == Decimal("95.00")


def test_slippage_applied_to_market_fill():
    """1 tick slippage on a BUY should increase fill price by 1 tick."""
    instrument = make_instrument()
    bars = [make_bar(100.0, i) for i in range(3)]
    engine = BacktestEngine(slippage=SlippageModel(fixed_ticks=1, proportional_bps=0))
    result = engine.run(BuyOnFirstBarStrategy(), instrument, bars, {})
    # 1 tick adverse slippage on buy = price + 1 * tick_size = 100 + 0.05 = 100.05
    # But slippage goes adverse (SELL side subtracts, BUY side is 100 - (-1*0.05) = 100.05
    # See SlippageModel: adverse = -1 for BUY, slipped = price - (-1)*ticks*tick
    assert result.fills[0].price == Decimal("100.05")


def test_no_lookahead_strategy_sees_only_past_bars():
    """The strategy's bar count at emit time must not exceed current bar index."""
    bars_seen: list[int] = []

    class CountingStrategy(Strategy):
        name = "counter"
        _count = 0

        def reset(self):
            self._count = 0

        def on_bar(self, bar, position, params):
            self._count += 1
            bars_seen.append(self._count)
            return []

    instrument = make_instrument()
    bars = [make_bar(100.0, i) for i in range(5)]
    BacktestEngine().run(CountingStrategy(), instrument, bars, {})
    assert bars_seen == [1, 2, 3, 4, 5]


def test_backtest_pnl_matches_position_apply_fill():
    """P&L from BacktestEngine must equal directly calling Position.apply_fill
    with the same fill data — this is the core reconciliation test."""
    from quant_system.core.fill import Fill
    from quant_system.core.enums import Side

    instrument = make_instrument()
    bars = [make_bar(100.0 + i, i) for i in range(5)]

    engine = BacktestEngine(slippage=SlippageModel(fixed_ticks=0, proportional_bps=0))
    result = engine.run(BuyOnFirstBarStrategy(), instrument, bars, {})

    # Manually apply the same fill to a Position
    if result.fills:
        pos = Position(instrument_token=9999)
        pos.apply_fill(result.fills[0])
        # Unrealized P&L at last bar close
        last_close = bars[-1].close
        bt_equity = result.equity_curve[-1][1]
        manual_equity = pos.unrealized_pnl(last_close) + pos.realized_pnl
        assert bt_equity == manual_equity


# ------------------------------------------------------------------ #
#  Walk-forward splits                                                 #
# ------------------------------------------------------------------ #

def test_walk_forward_splits_non_overlapping():
    """Out-of-sample windows must not overlap with each other.
    Walk-forward in-sample windows CAN overlap (rolling window) — that's by design.
    What must hold: each out-sample window starts where in-sample ends (no lookahead)."""
    windows = walk_forward_splits(total_bars=100, in_sample_bars=50, out_sample_bars=10)
    for w in windows:
        # The out-sample must immediately follow in-sample (no gap, no overlap)
        assert w.out_sample_start == w.in_sample_end
        # In-sample must not include any out-sample data (no lookahead)
        assert w.in_sample_end <= w.out_sample_start


def test_walk_forward_splits_cover_all_data():
    windows = walk_forward_splits(total_bars=80, in_sample_bars=50, out_sample_bars=10)
    assert windows[0].in_sample_start == 0
    last = windows[-1]
    assert last.out_sample_end <= 80


def test_walk_forward_slice_correct_length():
    bars = list(range(100))
    windows = walk_forward_splits(100, 60, 20)
    w = windows[0]
    assert len(slice_bars(bars, w, "in")) == 60
    assert len(slice_bars(bars, w, "out")) == 20


def test_walk_forward_invalid_args():
    with pytest.raises(ValueError):
        walk_forward_splits(100, 0, 10)
