"""Fill model — one immutable record per execution event.

Fills are the ground truth for both P&L and cost reconciliation. They are
never mutated after creation; corrections happen via new offsetting
records, never in-place edits, so the fill log stays a reliable audit trail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field

from quant_system.core.enums import Side


class Fill(BaseModel):
    model_config = {"frozen": True}

    fill_id: str  # broker execution id, or deterministic id in backtest
    client_order_id: str
    instrument_token: int
    side: Side

    quantity: int = Field(gt=0)
    price: Decimal = Field(gt=0)

    # Costs, itemized rather than lumped, so cost-model validation against
    # the exchange fee schedule can check each component independently.
    brokerage: Decimal = Decimal(0)
    stt_ctt: Decimal = Decimal(0)
    exchange_charges: Decimal = Decimal(0)
    gst: Decimal = Decimal(0)
    stamp_duty: Decimal = Decimal(0)

    slippage_vs_intent: Decimal | None = None  # signed: actual - intended price

    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "live"  # "live" | "backtest" — kept explicit to prevent mixing

    @property
    def gross_value(self) -> Decimal:
        return self.price * self.quantity

    @property
    def total_costs(self) -> Decimal:
        return self.brokerage + self.stt_ctt + self.exchange_charges + self.gst + self.stamp_duty

    @property
    def net_value(self) -> Decimal:
        """Cash impact of this fill including all costs. For a BUY this is
        cash out (positive magnitude spent); for a SELL this is cash in net
        of costs."""
        if self.side == Side.BUY:
            return self.gross_value + self.total_costs
        return self.gross_value - self.total_costs
