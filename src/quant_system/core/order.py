"""Order model and its lifecycle state machine.

The state machine is enforced here (not left to callers to get right)
because order-state bugs are exactly the kind of thing that "matches to the
paisa" reconciliation is designed to catch — better to fail loudly on an
illegal transition than to silently accept one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from quant_system.core.enums import OPEN_ORDER_STATUSES, OrderStatus, OrderType, Side


class IllegalOrderTransition(Exception):
    """Raised when code attempts to move an order between two statuses that
    are not a valid transition. This should never fire in correct code —
    if it does, treat it as a bug, not something to catch-and-ignore."""


# Allowed status -> {allowed next statuses}
_ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.SUBMITTED, OrderStatus.REJECTED, OrderStatus.UNKNOWN},
    OrderStatus.SUBMITTED: {
        OrderStatus.OPEN,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.REJECTED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.OPEN: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.CANCEL_REQUESTED: {
        OrderStatus.CANCELLED,
        OrderStatus.FILLED,  # race: fill and cancel request crossed
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.UNKNOWN,
    },
    # Terminal states: no legal outgoing transitions except via reconciliation
    # explicitly overriding into UNKNOWN, which is handled separately.
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.UNKNOWN: {s for s in OrderStatus},  # reconciliation can resolve UNKNOWN to anything
}


class Order(BaseModel):
    model_config = {"validate_assignment": True}

    client_order_id: str  # deterministic, see core.idempotency
    broker_order_id: str | None = None

    instrument_token: int
    side: Side
    order_type: OrderType
    quantity: int = Field(gt=0)  # in lots for derivatives, units for equity

    limit_price: Decimal | None = None
    trigger_price: Decimal | None = None  # for SL / SL_M orders

    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = Field(default=0, ge=0)
    average_fill_price: Decimal | None = None

    strategy_run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Free-form context for debugging/blotter — e.g. grid level, SAR leg id.
    tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_limit_price_present(self) -> "Order":
        if self.order_type in (OrderType.LIMIT, OrderType.SL) and self.limit_price is None:
            raise ValueError(f"{self.order_type} orders require a limit_price")
        if self.order_type in (OrderType.SL, OrderType.SL_M) and self.trigger_price is None:
            raise ValueError(f"{self.order_type} orders require a trigger_price")
        return self

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_ORDER_STATUSES

    def transition_to(self, new_status: OrderStatus, *, at: datetime | None = None) -> None:
        """Move the order to `new_status`, enforcing the legal-transition
        table. Raises IllegalOrderTransition on an invalid move instead of
        silently accepting it."""
        allowed = _ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise IllegalOrderTransition(
                f"Order {self.client_order_id}: illegal transition "
                f"{self.status} -> {new_status}"
            )
        self.status = new_status
        self.updated_at = at or datetime.now(timezone.utc)

    def apply_fill(self, fill_quantity: int, fill_price: Decimal, *, at: datetime | None = None) -> None:
        """Apply a fill event, updating filled_quantity and the running
        average fill price, and transitioning status accordingly."""
        if fill_quantity <= 0:
            raise ValueError("fill_quantity must be positive")
        if fill_quantity > self.remaining_quantity:
            raise ValueError(
                f"Order {self.client_order_id}: fill_quantity {fill_quantity} exceeds "
                f"remaining_quantity {self.remaining_quantity}"
            )

        prior_qty = self.filled_quantity
        prior_avg = self.average_fill_price or Decimal(0)
        new_qty = prior_qty + fill_quantity

        # Weighted-average fill price, exact in Decimal (no float drift).
        self.average_fill_price = (
            (prior_avg * prior_qty) + (fill_price * fill_quantity)
        ) / new_qty
        self.filled_quantity = new_qty

        target_status = (
            OrderStatus.FILLED if self.remaining_quantity == 0 else OrderStatus.PARTIALLY_FILLED
        )
        self.transition_to(target_status, at=at)
