"""Tick-to-bar aggregator.

Converts a stream of normalised Ticks into OHLCV bars of any resolution
(1-minute, 5-minute, daily etc.).

Key properties:
- Time-based bar boundaries (not tick-count based) — bars close on the
  clock boundary regardless of whether a tick arrived at exactly that moment.
- Partial bars: the aggregator always holds the current in-progress bar and
  emits it when the boundary is crossed.
- Gap handling: if no ticks arrive during a bar period, the previous close
  is carried forward (OHLC = prev_close, V = 0) — strategies need a bar
  for every period.
- Thread-safe: uses asyncio — no shared mutable state across coroutines.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator, Callable

from quant_system.data.base import Bar, Tick

logger = logging.getLogger(__name__)


class BarAggregator:
    """Aggregates ticks into fixed-duration OHLCV bars.

    Usage:
        agg = BarAggregator(resolution_seconds=60)
        async for bar in agg.aggregate(tick_stream):
            strategy.on_bar(bar, ...)
    """

    def __init__(self, resolution_seconds: int = 60) -> None:
        if resolution_seconds < 1:
            raise ValueError("resolution_seconds must be >= 1")
        self._resolution = timedelta(seconds=resolution_seconds)
        self._bars: dict[int, _PartialBar] = {}  # token -> in-progress bar

    async def aggregate(
        self,
        tick_stream: AsyncIterator[Tick],
        on_bar: Callable[[Bar], None] | None = None,
    ) -> AsyncIterator[Bar]:
        """Consume tick_stream and yield completed bars.

        If on_bar callback is provided, it is called synchronously on each
        completed bar in addition to yielding it.
        """
        async for tick in tick_stream:
            completed = self._process_tick(tick)
            for bar in completed:
                if on_bar:
                    on_bar(bar)
                yield bar

    def _process_tick(self, tick: Tick) -> list[Bar]:
        """Update in-progress bar with tick. Returns list of completed bars
        (usually 0 or 1, but >1 if gaps caused multiple empty bars)."""
        token = tick.instrument_token
        bar_start = self._floor_to_boundary(tick.timestamp)

        completed: list[Bar] = []

        if token not in self._bars:
            # First tick for this instrument
            self._bars[token] = _PartialBar(token, bar_start, tick)
            return completed

        partial = self._bars[token]

        if tick.timestamp >= partial.start + self._resolution:
            # Tick crossed into a new bar — close current bar
            completed.append(partial.to_bar())

            # Fill any gaps with carry-forward bars
            gap_start = partial.start + self._resolution
            while gap_start + self._resolution <= bar_start:
                completed.append(_PartialBar.carry_forward(token, gap_start, partial.close, tick.source).to_bar())
                gap_start += self._resolution

            # Start new bar
            self._bars[token] = _PartialBar(token, bar_start, tick)
        else:
            # Same bar — update
            partial.update(tick)

        return completed

    def flush(self) -> list[Bar]:
        """Emit all in-progress partial bars (call at session end)."""
        bars = [p.to_bar() for p in self._bars.values()]
        self._bars.clear()
        return bars

    def _floor_to_boundary(self, ts: datetime) -> datetime:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        seconds_since_epoch = int((ts - epoch).total_seconds())
        floored = seconds_since_epoch - (seconds_since_epoch % int(self._resolution.total_seconds()))
        return epoch + timedelta(seconds=floored)


class _PartialBar:
    """Mutable in-progress bar."""

    __slots__ = ("token", "start", "open", "high", "low", "close", "volume", "open_interest", "num_ticks", "source", "_tp_vol")

    def __init__(self, token: int, start: datetime, first_tick: Tick) -> None:
        self.token = token
        self.start = start
        self.open = first_tick.last_price
        self.high = first_tick.last_price
        self.low = first_tick.last_price
        self.close = first_tick.last_price
        self.volume = first_tick.last_quantity
        self.open_interest = first_tick.open_interest
        self.num_ticks = 1
        self.source = first_tick.source
        self._tp_vol = first_tick.last_price * first_tick.last_quantity  # for VWAP

    def update(self, tick: Tick) -> None:
        if tick.last_price > self.high:
            self.high = tick.last_price
        if tick.last_price < self.low:
            self.low = tick.last_price
        self.close = tick.last_price
        self.volume += tick.last_quantity
        self._tp_vol += tick.last_price * tick.last_quantity
        self.open_interest = tick.open_interest
        self.num_ticks += 1

    def to_bar(self) -> Bar:
        vwap = self._tp_vol / self.volume if self.volume > 0 else self.close
        return Bar(
            instrument_token=self.token,
            timestamp=self.start,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            open_interest=self.open_interest,
            vwap=vwap,
            num_ticks=self.num_ticks,
            source=self.source,
        )

    @classmethod
    def carry_forward(cls, token: int, start: datetime, prev_close: Decimal, source: str) -> "_PartialBar":
        """Create a zero-volume carry-forward bar for gap periods."""
        dummy_tick = type("T", (), {
            "last_price": prev_close, "last_quantity": 0,
            "open_interest": None, "source": source,
        })()
        obj = object.__new__(cls)
        obj.token = token
        obj.start = start
        obj.open = obj.high = obj.low = obj.close = prev_close
        obj.volume = 0
        obj.open_interest = None
        obj.num_ticks = 0
        obj.source = source
        obj._tp_vol = Decimal(0)
        return obj
