"""Volatility indicators: ATR, Bollinger Bands, Historical Volatility.

ATR is the primary indicator used by the Grid and SAR strategies for
spacing and stop levels — it is the single canonical implementation that
both strategies import; there is no second ATR anywhere in this codebase.
"""

from __future__ import annotations

import numpy as np

from quant_system.indicators.base import Indicator


class ATR(Indicator):
    """Average True Range (Wilder smoothing).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR[i]     = ((period-1) * ATR[i-1] + TR[i]) / period   (Wilder EMA)

    This is the authoritative ATR in the system. Grid spacing and SAR stop
    placement both call this; neither reimplements the calculation.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("ATR period must be >= 1")
        self._period = period

    @property
    def name(self) -> str:
        return f"ATR({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> np.ndarray:
        """Compute ATR from OHLC arrays. All arrays must be the same length."""
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)

        n = len(close)
        if n != len(high) or n != len(low):
            raise ValueError("high, low, close arrays must have equal length")

        tr = np.empty(n)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )

        atr = np.full(n, np.nan)
        if n < self._period:
            return atr

        # Seed: simple average of first `period` true ranges
        atr[self._period - 1] = np.mean(tr[: self._period])

        # Wilder smoothing
        alpha = 1.0 / self._period
        for i in range(self._period, n):
            atr[i] = atr[i - 1] * (1 - alpha) + tr[i] * alpha

        return atr


class BollingerBands(Indicator):
    """Bollinger Bands: middle (SMA), upper, lower bands.

    Returns a dict of three arrays: 'middle', 'upper', 'lower'.
    """

    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        self._period = period
        self._num_std = num_std

    @property
    def name(self) -> str:
        return f"BB({self._period},{self._num_std})"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, close: np.ndarray) -> dict[str, np.ndarray]:  # type: ignore[override]
        close = np.asarray(close, dtype=float)
        n = len(close)
        middle = np.full(n, np.nan)
        upper = np.full(n, np.nan)
        lower = np.full(n, np.nan)

        for i in range(self._period - 1, n):
            window = close[i - self._period + 1 : i + 1]
            m = np.mean(window)
            s = np.std(window, ddof=1)
            middle[i] = m
            upper[i] = m + self._num_std * s
            lower[i] = m - self._num_std * s

        return {"middle": middle, "upper": upper, "lower": lower}


class HistoricalVolatility(Indicator):
    """Annualised close-to-close historical volatility (log returns, std).

    HV[i] = std(log(close[i]/close[i-1]), ...) * sqrt(252)
    """

    def __init__(self, period: int = 20, ann_factor: float = 252.0) -> None:
        self._period = period
        self._ann_factor = ann_factor

    @property
    def name(self) -> str:
        return f"HV({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period + 1

    def compute(self, close: np.ndarray) -> np.ndarray:  # type: ignore[override]
        close = np.asarray(close, dtype=float)
        n = len(close)
        hv = np.full(n, np.nan)

        log_ret = np.full(n, np.nan)
        log_ret[1:] = np.log(close[1:] / close[:-1])

        for i in range(self._period, n):
            window = log_ret[i - self._period + 1 : i + 1]
            hv[i] = np.std(window, ddof=1) * np.sqrt(self._ann_factor)

        return hv
