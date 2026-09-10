"""Position model and the canonical P&L calculation.

This is the single realized/unrealized P&L implementation used by both the
live engine and the backtester (imported by both, never reimplemented) —
that shared code path is what "backtest reconciles to live" actually rests
on.

Method: weighted-average cost, signed quantity (positive = long, negative =
short). Handles pyramiding (adding to an existing position) and full
reversal (a fill that closes the existing position and opens the opposite
side) in one method, since grid/SAR strategies do both routinely.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from quant_system.core.enums import Side
from quant_system.core.fill import Fill


class Position(BaseModel):
    model_config = {"validate_assignment": True}

    instrument_token: int
    quantity: int = 0  # signed: + long, - short, 0 flat
    average_price: Decimal = Decimal(0)  # meaningless when quantity == 0

    realized_pnl: Decimal = Decimal(0)
    total_costs: Decimal = Decimal(0)

    pyramid_level: int = Field(default=0, ge=0)  # number of adds since flat

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        if self.is_flat:
            return Decimal(0)
        return (mark_price - self.average_price) * self.quantity

    def apply_fill(self, fill: Fill) -> None:
        """Update position state from a fill. Handles same-direction adds
        (pyramiding — weighted-average price update), reducing fills
        (partial or full close — realizes P&L on the closed portion), and
        reversing fills (a fill larger than the open position that flips
        the sign — realizes P&L on the closed leg, opens a fresh position
        on the remainder at the fill price)."""
        signed_fill_qty = fill.quantity if fill.side == Side.BUY else -fill.quantity
        self.total_costs += fill.total_costs

        # Flat -> opening a new position.
        if self.quantity == 0:
            self.quantity = signed_fill_qty
            self.average_price = fill.price
            self.pyramid_level = 0
            return

        same_direction = (self.quantity > 0) == (signed_fill_qty > 0)

        if same_direction:
            # Pyramiding: weighted-average the cost basis, exact in Decimal.
            new_qty = self.quantity + signed_fill_qty
            self.average_price = (
                (self.average_price * abs(self.quantity)) + (fill.price * abs(signed_fill_qty))
            ) / abs(new_qty)
            self.quantity = new_qty
            self.pyramid_level += 1
            return

        # Opposite direction: this fill reduces, closes, or reverses the position.
        closing_qty = min(abs(self.quantity), abs(signed_fill_qty))
        # Realized P&L on the closed portion, sign-correct for long vs short.
        direction = 1 if self.quantity > 0 else -1
        self.realized_pnl += (fill.price - self.average_price) * closing_qty * direction

        remaining_fill_qty = abs(signed_fill_qty) - closing_qty
        new_position_qty = self.quantity + signed_fill_qty

        if remaining_fill_qty > 0:
            # Reversal: closed the old position entirely and opened the
            # opposite side with whatever quantity was left over.
            self.quantity = new_position_qty
            self.average_price = fill.price
            self.pyramid_level = 0
        else:
            # Partial or exact close, same direction retained (or flat).
            self.quantity = new_position_qty
            if self.quantity == 0:
                self.average_price = Decimal(0)
                self.pyramid_level = 0
            # else: average_price unchanged on a partial close by design —
            # only the entry side moves the cost basis, not the exit side.
