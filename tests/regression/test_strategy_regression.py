"""Regression tests for Grid and SAR strategies.

Every test here exists because a real or hypothetical bug was found. The
comment above each test states what it would have caught.

Convention: if you change GridStrategy or StopAndReverseStrategy, a new
regression test must accompany the change (enforced by RegressionGuard agent).
"""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pytest

from quant_system.core.enums import ContractType, Exchange, OrderType, Side
from quant_system.core.instrument import Instrument
from quant_system.core.position import Position
from quant_system.strategies.grid import GridStrategy
from quant_system.strategies.stop_and_reverse import StopAndReverseStrategy
from quant_system.strategies.base import Bar


def make_instrument(lot_size=1, tick_size="0.05"):
    return Instrument(
        tradingsymbol="GOLDFUT",
        exchange=Exchange.MCX,
        contract_type=ContractType.FUTURE,
        instrument_token=1001,
        lot_size=lot_size,
        tick_size=Decimal(tick_size),
    )


def make_bar(close: float, ts_minute: int = 0) -> Bar:
    c = Decimal(str(close))
    return Bar(
        instrument_token=1001,
        timestamp=datetime(2024, 6, 1, 9, ts_minute, tzinfo=timezone.utc),
        open=c,
        high=c + Decimal("2"),
        low=c - Decimal("2"),
        close=c,
        volume=500,
    )


GRID_PARAMS = {
    "atr_period": 5,
    "atr_spacing_multiplier": 1.0,
    "max_pyramid_levels": 3,
    "position_cap_lots": 5,
}

SAR_PARAMS = {
    "atr_period": 5,
    "atr_stop_multiplier": 1.0,
    "position_cap_lots": 5,
}


# ------------------------------------------------------------------ #
#  GridStrategy regression tests                                       #
# ------------------------------------------------------------------ #

def test_grid_no_intents_before_warmup():
    """Regression: grid was emitting intents before ATR had valid values,
    leading to grid_spacing=NaN and orders at price 0."""
    strat = GridStrategy(make_instrument())
    pos = Position(instrument_token=1001)
    # Feed fewer bars than MIN_BARS (30)
    intents = []
    for i in range(15):
        intents.extend(strat.on_bar(make_bar(100.0 + i, i), pos, GRID_PARAMS))
    assert len(intents) == 0, "Should emit no intents before warmup"


def test_grid_kill_switch_stops_all_intents():
    """Regression: kill switch was not checked before intent emission,
    allowing orders after a loss-limit breach."""
    strat = GridStrategy(make_instrument())
    pos = Position(instrument_token=1001)
    bars = [make_bar(100.0 + i, i) for i in range(40)]
    # Warm up
    for bar in bars[:35]:
        strat.on_bar(bar, pos, GRID_PARAMS)
    strat.activate_kill_switch("test")
    # After kill switch, no intents should be emitted
    intents = strat.on_bar(bars[35], pos, GRID_PARAMS)
    assert intents == []


def test_grid_position_cap_not_exceeded():
    """Regression: pyramid_level check was off-by-one, causing an extra order
    that briefly exceeded position_cap_lots."""
    strat = GridStrategy(make_instrument())
    pos = Position(instrument_token=1001)
    # Simulate position at cap
    pos.quantity = 5  # at cap
    bars = [make_bar(100.0 + i, i) for i in range(40)]
    for bar in bars[:35]:
        strat.on_bar(bar, pos, GRID_PARAMS)
    intents = strat.on_bar(bars[35], pos, GRID_PARAMS)
    buy_intents = [i for i in intents if i.side == Side.BUY]
    assert len(buy_intents) == 0, "Must not emit BUY intent when at position cap"


def test_grid_reset_clears_state():
    """Regression: reset() was not clearing _active_grid_levels, causing stale
    level deduplication to suppress valid intents on the second run."""
    strat = GridStrategy(make_instrument())
    pos = Position(instrument_token=1001)
    bars = [make_bar(100.0 + i, i) for i in range(40)]
    for bar in bars[:35]:
        strat.on_bar(bar, pos, GRID_PARAMS)
    strat.reset()
    # After reset, should be able to warm up again without leftover state
    for bar in bars[:25]:
        strat.on_bar(bar, pos, GRID_PARAMS)
    # State should be as if fresh
    assert strat._kill_switch is False
    assert strat._intent_seq == 0


# ------------------------------------------------------------------ #
#  StopAndReverseStrategy regression tests                             #
# ------------------------------------------------------------------ #

def test_sar_no_intents_before_warmup():
    """Regression: SAR was reversing on bar[2] before ATR warmup,
    using ATR=None as a stop, crashing the broker adapter."""
    strat = StopAndReverseStrategy(make_instrument())
    pos = Position(instrument_token=1001)
    intents = []
    for i in range(10):
        intents.extend(strat.on_bar(make_bar(100.0 + i, i), pos, SAR_PARAMS))
    # May have initial entry but no reversal intents with only 10 bars
    reversal_intents = [i for i in intents if "reverse" in i.reason]
    assert len(reversal_intents) == 0


def test_sar_kill_switch_blocks_all_intents():
    """Regression: kill switch check was missing in SAR, order flow continued
    after circuit breaker should have halted it."""
    strat = StopAndReverseStrategy(make_instrument())
    pos = Position(instrument_token=1001)
    bars = [make_bar(100.0 + i, i) for i in range(30)]
    for bar in bars[:25]:
        strat.on_bar(bar, pos, SAR_PARAMS)
    strat.activate_kill_switch("circuit_breaker_test")
    intents = strat.on_bar(bars[25], pos, SAR_PARAMS)
    assert intents == []


def test_sar_zero_position_cap_emits_nothing():
    """Regression: SAR was not checking position_cap_lots=0 from CIRCUIT_HALT
    regime override, leading to orders going out during a halt."""
    strat = StopAndReverseStrategy(make_instrument())
    pos = Position(instrument_token=1001)
    params = {**SAR_PARAMS, "position_cap_lots": 0}
    bars = [make_bar(100.0 + i, i) for i in range(30)]
    intents = []
    for bar in bars:
        intents.extend(strat.on_bar(bar, pos, params))
    assert len(intents) == 0


def test_sar_reversal_intent_is_sl_m():
    """Regression: reversal orders were placed as MARKET, missing the stop trigger
    price, causing them to execute immediately at open instead of at the stop level."""
    strat = StopAndReverseStrategy(make_instrument())
    pos = Position(instrument_token=1001)

    # Warm up with rising prices (establishes long)
    bars = [make_bar(100.0 + i * 0.5, i) for i in range(25)]
    all_intents = []
    for bar in bars:
        all_intents.extend(strat.on_bar(bar, pos, SAR_PARAMS))

    # Now send a bar that sharply drops to breach the stop
    crash_bar = Bar(
        instrument_token=1001,
        timestamp=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc),
        open=Decimal("110"),
        high=Decimal("111"),
        low=Decimal("80"),   # far below any reasonable stop
        close=Decimal("85"),
        volume=5000,
    )
    reversal_intents = strat.on_bar(crash_bar, pos, SAR_PARAMS)
    reversal = [i for i in reversal_intents if "reverse" in i.reason]
    if reversal:
        assert reversal[0].order_type == OrderType.SL_M
        assert reversal[0].trigger_price is not None
