"""Stop-and-Reverse (SAR) strategy engine.

When the current stop is hit, the position is flipped in the opposite
direction. Uses ATR-based stops: stop distance = atr_stop_multiplier * ATR.

The SAR is always in the market (long or short), never flat, except at
startup before the first signal fires or when the kill switch is active.

Position cap is enforced: SAR always trades exactly 1 unit (no pyramiding),
capped at position_cap_lots from config.
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

_REQUIRED_PARAMS = {"atr_period", "atr_stop_multiplier", "position_cap_lots"}


class StopAndReverseStrategy(Strategy):
    """ATR-based Stop-and-Reverse.

    Emits a reverse intent when the trailing stop is breached.
    The execution engine handles converting the intent into a stop-order
    (SL_M type) so the reversal is atomic at the broker.
    """

    _MIN_BARS = 20

    def __init__(self, instrument: Instrument, history_size: int = 500) -> None:
        self._instrument = instrument
        self._history_size = history_size
        self._kill_switch = False
        self._intent_seq = 0
        self._current_stop: Decimal | None = None
        self._current_direction: Side | None = None  # current intended position side

        self._highs: deque[float] = deque(maxlen=history_size)
        self._lows: deque[float] = deque(maxlen=history_size)
        self._closes: deque[float] = deque(maxlen=history_size)

    @property
    def name(self) -> str:
        return f"SAR({self._instrument.tradingsymbol})"

    def reset(self) -> None:
        self._kill_switch = False
        self._intent_seq = 0
        self._current_stop = None
        self._current_direction = None
        self._highs.clear()
        self._lows.clear()
        self._closes.clear()

    def activate_kill_switch(self, reason: str = "") -> None:
        logger.warning(
            "sar_kill_switch_activated",
            extra={"instrument": self._instrument.tradingsymbol, "reason": reason},
        )
        self._kill_switch = True

    def deactivate_kill_switch(self) -> None:
        self._kill_switch = False

    def on_bar(self, bar: Bar, position: Position, strategy_params: dict) -> list[OrderIntent]:
        if self._kill_switch:
            return []

        self._validate_params(strategy_params)
        self._update_history(bar)

        if len(self._closes) < self._MIN_BARS:
            return []

        position_cap = int(strategy_params["position_cap_lots"])
        if position_cap == 0:
            return []

        atr_period = int(strategy_params["atr_period"])
        stop_mult = float(strategy_params["atr_stop_multiplier"])

        atr_val = self._current_atr(atr_period)
        if atr_val is None or atr_val <= 0:
            return []

        atr_dec = Decimal(str(atr_val))
        stop_distance = self._instrument.round_to_tick(atr_dec * Decimal(str(stop_mult)))
        current_price = bar.close
        intents: list[OrderIntent] = []

        # --- Initial entry when flat ---
        if position.is_flat and self._current_direction is None:
            # Enter long initially (first direction choice; regime can override this)
            self._current_direction = Side.BUY
            self._current_stop = self._instrument.round_to_tick(current_price - stop_distance)
            self._intent_seq += 1
            intents.append(OrderIntent(
                instrument=self._instrument,
                side=Side.BUY,
                order_type=OrderType.MARKET,
                quantity=min(1, position_cap),
                intent_sequence=self._intent_seq,
                reason="sar_initial_entry_long",
                tags={"strategy": "sar", "action": "initial_entry"},
            ))
            return intents

        # --- Update trailing stop ---
        if self._current_direction == Side.BUY and self._current_stop is not None:
            new_stop = self._instrument.round_to_tick(current_price - stop_distance)
            if new_stop > self._current_stop:
                self._current_stop = new_stop

        elif self._current_direction == Side.SELL and self._current_stop is not None:
            new_stop = self._instrument.round_to_tick(current_price + stop_distance)
            if new_stop < self._current_stop:
                self._current_stop = new_stop

        # --- Check for stop breach → reversal ---
        if self._current_stop is None:
            return []

        stop_breached = False
        if self._current_direction == Side.BUY and bar.low <= self._current_stop:
            stop_breached = True
            logger.info(
                "sar_stop_breached_long",
                extra={
                    "instrument": self._instrument.tradingsymbol,
                    "stop": str(self._current_stop),
                    "low": str(bar.low),
                },
            )
        elif self._current_direction == Side.SELL and bar.high >= self._current_stop:
            stop_breached = True
            logger.info(
                "sar_stop_breached_short",
                extra={
                    "instrument": self._instrument.tradingsymbol,
                    "stop": str(self._current_stop),
                    "high": str(bar.high),
                },
            )

        if stop_breached:
            # Flip direction
            new_direction = Side.SELL if self._current_direction == Side.BUY else Side.BUY
            reversal_qty = abs(position.quantity) + min(1, position_cap)  # close current + open opposite

            self._intent_seq += 1
            intents.append(OrderIntent(
                instrument=self._instrument,
                side=new_direction,
                order_type=OrderType.SL_M,
                quantity=reversal_qty,
                trigger_price=self._current_stop,
                intent_sequence=self._intent_seq,
                reason=f"sar_reverse_to_{'long' if new_direction == Side.BUY else 'short'}",
                tags={
                    "strategy": "sar",
                    "action": "reversal",
                    "stop_price": str(self._current_stop),
                },
            ))

            # Update direction and reset stop for the new side
            self._current_direction = new_direction
            if new_direction == Side.BUY:
                self._current_stop = self._instrument.round_to_tick(current_price - stop_distance)
            else:
                self._current_stop = self._instrument.round_to_tick(current_price + stop_distance)

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
        vals = ATR(period).compute(
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
            raise ValueError(f"SARStrategy missing params: {missing}")
