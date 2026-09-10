"""Abstract broker interface.

Both the Kite live adapter and the backtest paper-trading adapter implement
this interface. Execution engine code only talks to AbstractBroker — it has
no Kite-specific imports and runs identically in backtest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import AsyncIterator

from quant_system.core.fill import Fill
from quant_system.core.instrument import Instrument
from quant_system.core.order import Order


class AbstractBroker(ABC):
    """Minimal async broker interface."""

    @abstractmethod
    async def place_order(self, order: Order) -> str:
        """Submit order to broker. Returns broker_order_id.
        Must be idempotent on duplicate client_order_id."""
        ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Request order cancellation. Returns True if cancel was accepted."""
        ...

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> Order:
        """Fetch current order state from broker."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[dict]:
        """Fetch current positions from broker (raw broker format)."""
        ...

    @abstractmethod
    async def stream_ticks(self, instrument_tokens: list[int]) -> AsyncIterator[dict]:
        """Subscribe to live tick stream. Yields raw tick dicts."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection (REST auth + WebSocket open)."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close all connections."""
        ...
