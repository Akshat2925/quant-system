"""Tick data pipeline — vendor-agnostic normalisation layer.

Supported feeds:
    TrueDataFeed    — TrueData WebSocket (MCX + NSE)
    GDFLFeed        — GDFL TCP socket (MCX specialist)
    HistoricalBarFeed — Local Parquet/CSV for backtesting

All feeds produce normalised Tick objects.
BarAggregator converts ticks into OHLCV bars at any resolution.
"""

from quant_system.data.bar_aggregator import BarAggregator
from quant_system.data.base import Bar, Tick, TickFeed
from quant_system.data.gdfl_feed import GDFLFeed
from quant_system.data.historical import HistoricalBarFeed
from quant_system.data.truedata_feed import TrueDataFeed

__all__ = [
    "Bar",
    "BarAggregator",
    "GDFLFeed",
    "HistoricalBarFeed",
    "Tick",
    "TickFeed",
    "TrueDataFeed",
]
