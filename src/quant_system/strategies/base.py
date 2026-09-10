"""Base strategy interface.

All strategies produce OrderIntent objects. The execution engine converts
intents into actual orders — strategies never touch the broker directly.
This separation means the same strategy code runs identically in backtest
and live, with only the execution layer swapped.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from quant_system.core.enums import OrderType, Side
from quant_system.core.instrument import Instrument
from quant_system.core.position import Position


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar. The strategy only sees bars, never ticks directly."""

    instrument_token: int
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class OrderIntent:
    """A strategy's request to place an order.

    The execution engine validates, sizes (applying risk caps), and routes
    this into actual Order objects. Strategies emit intents; they do not
    create Order objects directly.
    """

    instrument: Instrument
    side: Side
    order_type: OrderType
    quantity: int                      # in lots; execution engine may reduce for risk caps
    limit_price: Decimal | None = None
    trigger_price: Decimal | None = None
    intent_sequence: int = 0
    reason: str = ""                   # human-readable, e.g. "grid_level_3_buy"
    tags: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Strategy(ABC):
    """Abstract base for all strategies."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def on_bar(
        self,
        bar: Bar,
        position: Position,
        strategy_params: dict,
    ) -> list[OrderIntent]:
        """Called on each new bar. Returns zero or more order intents."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset internal state (called at start of each backtest run)."""
        ...
