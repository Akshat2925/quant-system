"""Historical data feed — reads from Parquet/CSV files for backtesting.

Implements the same TickFeed interface as live feeds so the backtesting
pipeline is identical to the live pipeline, with only the feed swapped.

Supported formats:
- Parquet (preferred — columnar, fast, small)
- CSV (fallback)

File naming convention:
    data/{exchange}/{symbol}/{YYYY-MM-DD}.parquet
    e.g. data/MCX/GOLDM25DECFUT/2024-06-01.parquet

Expected columns:
    timestamp, open, high, low, close, volume, open_interest (optional)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import AsyncIterator

from quant_system.data.base import Bar, Tick, TickFeed

logger = logging.getLogger(__name__)


class HistoricalBarFeed:
    """Reads OHLCV bars from Parquet/CSV files.

    Used by BacktestEngine when historical data is available locally.
    Yields Bar objects in chronological order.
    """

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)

    def load_bars(
        self,
        symbol: str,
        exchange: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Load bars for a symbol between start and end dates."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas required for HistoricalBarFeed")

        symbol_dir = self._data_dir / exchange / symbol
        if not symbol_dir.exists():
            logger.warning("historical_data_dir_not_found", extra={"path": str(symbol_dir)})
            return []

        frames = []
        for f in sorted(symbol_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(f)
                frames.append(df)
            except Exception:
                pass

        for f in sorted(symbol_dir.glob("*.csv")):
            try:
                df = pd.read_csv(f, parse_dates=["timestamp"])
                frames.append(df)
            except Exception:
                pass

        if not frames:
            return []

        data = pd.concat(frames, ignore_index=True)
        data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
        data = data.sort_values("timestamp")
        data = data[
            (data["timestamp"] >= pd.Timestamp(start)) &
            (data["timestamp"] <= pd.Timestamp(end))
        ]

        bars = []
        for _, row in data.iterrows():
            bars.append(Bar(
                instrument_token=int(row.get("instrument_token", 0)),
                timestamp=row["timestamp"].to_pydatetime(),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=int(row["volume"]),
                open_interest=int(row["open_interest"]) if "open_interest" in row and not pd.isna(row["open_interest"]) else None,
                source="historical",
            ))
        return bars

    def save_bars(self, bars: list[Bar], symbol: str, exchange: str) -> Path:
        """Save bars to Parquet format."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas required for HistoricalBarFeed")

        out_dir = self._data_dir / exchange / symbol
        out_dir.mkdir(parents=True, exist_ok=True)

        records = [{
            "timestamp": b.timestamp,
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": b.volume,
            "open_interest": b.open_interest,
            "instrument_token": b.instrument_token,
        } for b in bars]

        df = pd.DataFrame(records)
        date_str = bars[0].timestamp.strftime("%Y-%m-%d") if bars else "unknown"
        out_path = out_dir / f"{date_str}.parquet"
        df.to_parquet(out_path, index=False)
        logger.info("bars_saved", extra={"path": str(out_path), "count": len(bars)})
        return out_path
