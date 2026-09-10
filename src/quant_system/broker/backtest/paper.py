"""Paper-trading broker adapter for backtest/simulation.

Implements AbstractBroker with an in-memory order book. Used by the
execution engine in backtest mode — the execution engine code is identical
to live mode, only the broker adapter is swapped.

This adapter does NOT simulate fills (that's the BacktestEngine's job).
It provides a synchronous, always-available broker interface so execution
engine integration tests can run without any Kite credentials.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator

from quant_system.broker.base import AbstractBroker
from quant_system.core.enums import OrderStatus
from quant_system.core.order import Order


class PaperBroker(AbstractBroker):
    """In-memory paper broker for testing and backtesting."""

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}

    async def connect(self) -> None:
        pass  # no-op

    async def disconnect(self) -> None:
        pass  # no-op

    async def place_order(self, order: Order) -> str:
        broker_id = f"paper_{uuid.uuid4().hex[:12]}"
        order.broker_order_id = broker_id
        order.transition_to(OrderStatus.SUBMITTED)
        order.transition_to(OrderStatus.OPEN)
        self._orders[broker_id] = order
        return broker_id

    async def cancel_order(self, broker_order_id: str) -> bool:
        order = self._orders.get(broker_order_id)
        if order and order.is_open:
            order.transition_to(OrderStatus.CANCELLED)
            return True
        return False

    async def get_order_status(self, broker_order_id: str) -> Order:
        order = self._orders.get(broker_order_id)
        if order is None:
            raise KeyError(f"Unknown broker_order_id: {broker_order_id}")
        return order

    async def get_positions(self) -> list[dict]:
        return []  # paper broker has no live positions; backtest engine tracks them

    async def stream_ticks(self, instrument_tokens: list[int]) -> AsyncIterator[dict]:
        # Paper broker yields nothing; backtest feeds bars directly to strategy
        return
        yield  # make this an async generator
