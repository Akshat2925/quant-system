"""Abstract tick data feed interface.

All tick vendors (TrueData, GDFL, Kite WebSocket) implement this interface.
The strategy and indicator layer only see normalised `Tick` and `Bar` objects —
never vendor-specific dicts.

This separation means swapping vendors (e.g. from GDFL to TrueData) is a
one-file change with no impact on strategies or backtests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator


@dataclass(frozen=True)
class Tick:
    """Normalised tick — one trade or quote update from any vendor.

    All vendors are normalised to this structure before entering the system.
    Price is Decimal for MCX contracts where paisa-level precision matters.
    """

    instrument_token: int
    timestamp: datetime
    last_price: Decimal
    last_quantity: int
    volume: int                        # cumulative volume for the session
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_qty: int | None = None
    ask_qty: int | None = None
    open_interest: int | None = None   # critical for F&O regime signals
    source: str = "live"               # "truedata" | "gdfl" | "kite" | "backtest"


@dataclass
class Bar:
    """OHLCV bar aggregated from ticks."""

    instrument_token: int
    timestamp: datetime                # bar open time
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    open_interest: int | None = None
    vwap: Decimal | None = None
    num_ticks: int = 0
    source: str = "live"


class TickFeed(ABC):
    """Abstract real-time tick feed."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def subscribe(self, instrument_tokens: list[int]) -> None: ...

    @abstractmethod
    async def stream(self) -> AsyncIterator[Tick]:
        """Yield normalised ticks as they arrive."""
        ...
