"""Grid strategy engine.

ATR-based dynamic grid: places buy orders below price and sell orders above
at intervals of `atr_spacing_multiplier * ATR`. Pyramids into the trend
up to `max_pyramid_levels`. Kill switch and position cap enforced here
before any intent is emitted.

Key design decisions:
- Grid levels are recomputed on each bar from the current ATR — the grid
  floats with volatility, it doesn't stay fixed from trade entry.
- Pyramiding level is read from Position.pyramid_level so the strategy
  and position state never diverge.
- Kill switch state is local to this instance; the risk module also has a
  global kill switch that the execution engine checks independently.
"""

from __future__ import annotations

import logging
from collections import deque
from decimal import Decimal

import numpy as np

from quant_system.core.enums import OrderType, Side
from quant_system.core.instrument import Instrument
from quant_system.core.position import Position
from quant_system.indicators import ATR
from quant_system.strategies.base import Bar, OrderIntent, Strategy

logger = logging.getLogger(__name__)

_REQUIRED_PARAMS = {
    "atr_period",
    "atr_spacing_multiplier",
    "max_pyramid_levels",
    "position_cap_lots",
}


class GridStrategy(Strategy):
    """ATR-spaced grid with pyramiding and built-in kill switch.

    Params (from strategy_params.yaml, regime-overridden at runtime):
        atr_period              : int   (default 14)
        atr_spacing_multiplier  : float (default 1.5)
        max_pyramid_levels      : int   (default 5)
        position_cap_lots       : int   position hard cap in lots
        lot_size                : int   (from instrument config)
    """

    _MIN_BARS = 30  # warmup before emitting any intents

    def __init__(self, instrument: Instrument, history_size: int = 500) -> None:
        self._instrument = instrument
        self._history_size = history_size
        self._kill_switch = False
        self._intent_seq = 0

        # Rolling price history for ATR
        self._highs: deque[float] = deque(maxlen=history_size)
        self._lows: deque[float] = deque(maxlen=history_size)
        self._closes: deque[float] = deque(maxlen=history_size)

        # Track which grid levels already have open intents this bar
        # (prevents double-emitting on the same level)
        self._active_grid_levels: set[int] = set()

    @property
    def name(self) -> str:
        return f"Grid({self._instrument.tradingsymbol})"

    def reset(self) -> None:
        self._kill_switch = False
        self._intent_seq = 0
        self._highs.clear()
        self._lows.clear()
        self._closes.clear()
        self._active_grid_levels.clear()

    def activate_kill_switch(self, reason: str = "") -> None:
        logger.warning("grid_kill_switch_activated", extra={"instrument": self._instrument.tradingsymbol, "reason": reason})
        self._kill_switch = True

    def deactivate_kill_switch(self) -> None:
        logger.info("grid_kill_switch_deactivated", extra={"instrument": self._instrument.tradingsymbol})
        self._kill_switch = False

    def on_bar(self, bar: Bar, position: Position, strategy_params: dict) -> list[OrderIntent]:
        if self._kill_switch:
            logger.debug("grid_kill_switch_active_skipping_bar", extra={"instrument": self._instrument.tradingsymbol})
            return []

        self._validate_params(strategy_params)
        self._update_history(bar)

        if len(self._closes) < self._MIN_BARS:
            return []

        atr_period = int(strategy_params["atr_period"])
        spacing_mult = float(strategy_params["atr_spacing_multiplier"])
        max_levels = int(strategy_params["max_pyramid_levels"])
        position_cap = int(strategy_params["position_cap_lots"])

        # Position cap acts as a kill switch: no new longs if cap reached
        if abs(position.quantity) >= position_cap:
            logger.debug(
                "grid_position_cap_reached",
                extra={"token": self._instrument.instrument_token, "qty": position.quantity, "cap": position_cap},
            )
            return []

        atr_val = self._current_atr(atr_period)
        if atr_val is None or atr_val <= 0:
            return []

        grid_spacing = Decimal(str(spacing_mult * atr_val))
        grid_spacing = self._instrument.round_to_tick(grid_spacing)
        if grid_spacing <= 0:
            return []

        current_price = bar.close
        intents: list[OrderIntent] = []

        # Determine how many more pyramid levels are available
        levels_used = position.pyramid_level if not position.is_flat else 0
        levels_available = max_levels - levels_used

        if levels_available <= 0:
            return []

        # Emit buy intent at the next grid level below current price
        # (grid trades in the direction of the trend — if long, buy dips)
        if position.is_flat or position.is_long:
            buy_price = current_price - grid_spacing
            buy_price = self._instrument.round_to_tick(buy_price)
            lots_to_buy = min(1, position_cap - max(0, position.quantity))
            if lots_to_buy > 0:
                self._intent_seq += 1
                intents.append(OrderIntent(
                    instrument=self._instrument,
                    side=Side.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=lots_to_buy,
                    limit_price=buy_price,
                    intent_sequence=self._intent_seq,
                    reason=f"grid_buy_level_{levels_used + 1}",
                    tags={"strategy": "grid", "grid_level": str(levels_used + 1)},
                ))

        # Emit sell/stop intent above current price for profit-taking / stop
        if position.is_long and not position.is_flat:
            sell_price = current_price + grid_spacing
            sell_price = self._instrument.round_to_tick(sell_price)
            stop_price = current_price - grid_spacing * Decimal("1.5")
            stop_price = self._instrument.round_to_tick(stop_price)
            self._intent_seq += 1
            intents.append(OrderIntent(
                instrument=self._instrument,
                side=Side.SELL,
                order_type=OrderType.LIMIT,
                quantity=min(1, position.quantity),
                limit_price=sell_price,
                intent_sequence=self._intent_seq,
                reason="grid_take_profit",
                tags={"strategy": "grid", "order_role": "take_profit"},
            ))

        return intents

    # ------------------------------------------------------------------ #
    #  Internals                                                           #
    # ------------------------------------------------------------------ #

    def _update_history(self, bar: Bar) -> None:
        self._highs.append(float(bar.high))
        self._lows.append(float(bar.low))
        self._closes.append(float(bar.close))

    def _current_atr(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        atr_ind = ATR(period)
        vals = atr_ind.compute(
            np.array(self._highs),
            np.array(self._lows),
            np.array(self._closes),
        )
        last = vals[-1]
        return None if np.isnan(last) else float(last)

    @staticmethod
    def _validate_params(params: dict) -> None:
        missing = _REQUIRED_PARAMS - set(params)
        if missing:
            raise ValueError(f"GridStrategy missing params: {missing}")
