"""Trend indicators: EMA, SMA, MACD, ADX, Supertrend.

Single implementation each — strategies import from here, never reimplement.
"""

from __future__ import annotations

import numpy as np

from quant_system.indicators.base import Indicator
from quant_system.indicators.volatility import ATR


class SMA(Indicator):
    """Simple Moving Average."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("SMA period must be >= 1")
        self._period = period

    @property
    def name(self) -> str:
        return f"SMA({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, close: np.ndarray) -> np.ndarray:
        close = np.asarray(close, dtype=float)
        n = len(close)
        out = np.full(n, np.nan)
        for i in range(self._period - 1, n):
            out[i] = np.mean(close[i - self._period + 1 : i + 1])
        return out


class EMA(Indicator):
    """Exponential Moving Average (standard multiplier: 2/(period+1))."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("EMA period must be >= 1")
        self._period = period
        self._k = 2.0 / (period + 1)

    @property
    def name(self) -> str:
        return f"EMA({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, close: np.ndarray) -> np.ndarray:
        close = np.asarray(close, dtype=float)
        n = len(close)
        out = np.full(n, np.nan)
        if n < self._period:
            return out

        # Seed with SMA of first `period` bars
        out[self._period - 1] = np.mean(close[: self._period])
        for i in range(self._period, n):
            out[i] = close[i] * self._k + out[i - 1] * (1 - self._k)
        return out


class MACD(Indicator):
    """MACD: fast EMA - slow EMA, signal line, histogram.

    Returns dict: 'macd', 'signal', 'histogram'.
    """

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self._fast = EMA(fast)
        self._slow = EMA(slow)
        self._signal_period = signal
        self._signal_ema = EMA(signal)

    @property
    def name(self) -> str:
        return f"MACD({self._fast._period},{self._slow._period},{self._signal_period})"

    @property
    def warmup_period(self) -> int:
        return self._slow._period + self._signal_period

    def compute(self, close: np.ndarray) -> dict[str, np.ndarray]:  # type: ignore[override]
        fast_vals = self._fast.compute(close)
        slow_vals = self._slow.compute(close)

        macd_line = np.where(
            np.isnan(fast_vals) | np.isnan(slow_vals),
            np.nan,
            fast_vals - slow_vals,
        )

        # Signal line: EMA of MACD line (skip NaNs for seeding)
        valid_idx = np.where(~np.isnan(macd_line))[0]
        signal_line = np.full(len(close), np.nan)
        if len(valid_idx) >= self._signal_period:
            sig_vals = self._signal_ema.compute(macd_line[valid_idx])
            for i, idx in enumerate(valid_idx):
                signal_line[idx] = sig_vals[i]

        histogram = np.where(
            np.isnan(macd_line) | np.isnan(signal_line),
            np.nan,
            macd_line - signal_line,
        )
        return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


class ADX(Indicator):
    """Average Directional Index (Wilder). Also computes +DI and -DI.

    Returns dict: 'adx', 'plus_di', 'minus_di'.
    ADX >= 25 is conventionally "trending"; < 20 is "non-trending".
    """

    def __init__(self, period: int = 14) -> None:
        self._period = period

    @property
    def name(self) -> str:
        return f"ADX({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period * 2

    def compute(  # type: ignore[override]
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> dict[str, np.ndarray]:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        n = len(close)

        atr_vals = ATR(self._period).compute(high, low, close)

        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        for i in range(1, n):
            up_move = high[i] - high[i - 1]
            down_move = low[i - 1] - low[i]
            plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

        alpha = 1.0 / self._period
        smooth_plus = np.full(n, np.nan)
        smooth_minus = np.full(n, np.nan)

        if n >= self._period:
            smooth_plus[self._period - 1] = np.sum(plus_dm[: self._period])
            smooth_minus[self._period - 1] = np.sum(minus_dm[: self._period])
            for i in range(self._period, n):
                smooth_plus[i] = smooth_plus[i - 1] * (1 - alpha) + plus_dm[i]
                smooth_minus[i] = smooth_minus[i - 1] * (1 - alpha) + minus_dm[i]

        plus_di = np.where(atr_vals > 0, 100.0 * smooth_plus / atr_vals, np.nan)
        minus_di = np.where(atr_vals > 0, 100.0 * smooth_minus / atr_vals, np.nan)

        dx = np.where(
            (plus_di + minus_di) > 0,
            100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di),
            np.nan,
        )

        adx = np.full(n, np.nan)
        first_adx = self._period * 2 - 2
        if n > first_adx:
            adx[first_adx] = np.nanmean(dx[self._period - 1 : first_adx + 1])
            for i in range(first_adx + 1, n):
                if not np.isnan(adx[i - 1]) and not np.isnan(dx[i]):
                    adx[i] = adx[i - 1] * (1 - alpha) + dx[i] * alpha

        return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


class Supertrend(Indicator):
    """Supertrend indicator: ATR-based trend-following stop/trail line.

    Returns dict: 'supertrend' (price level), 'direction' (+1=up, -1=down).
    Widely used in Indian retail trading; included because SAR strategies
    often reference it for secondary confirmation.
    """

    def __init__(self, period: int = 10, multiplier: float = 3.0) -> None:
        self._period = period
        self._multiplier = multiplier
        self._atr = ATR(period)

    @property
    def name(self) -> str:
        return f"Supertrend({self._period},{self._multiplier})"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(  # type: ignore[override]
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> dict[str, np.ndarray]:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        n = len(close)

        atr = self._atr.compute(high, low, close)
        hl2 = (high + low) / 2.0

        basic_upper = hl2 + self._multiplier * atr
        basic_lower = hl2 - self._multiplier * atr

        final_upper = np.full(n, np.nan)
        final_lower = np.full(n, np.nan)
        supertrend = np.full(n, np.nan)
        direction = np.full(n, np.nan)

        for i in range(self._period, n):
            # Upper band
            if np.isnan(final_upper[i - 1]) or basic_upper[i] < final_upper[i - 1]:
                final_upper[i] = basic_upper[i]
            else:
                final_upper[i] = final_upper[i - 1] if close[i - 1] <= final_upper[i - 1] else basic_upper[i]

            # Lower band
            if np.isnan(final_lower[i - 1]) or basic_lower[i] > final_lower[i - 1]:
                final_lower[i] = basic_lower[i]
            else:
                final_lower[i] = final_lower[i - 1] if close[i - 1] >= final_lower[i - 1] else basic_lower[i]

            # Direction
            if np.isnan(supertrend[i - 1]):
                direction[i] = -1.0
                supertrend[i] = final_upper[i]
            elif supertrend[i - 1] == final_upper[i - 1]:
                if close[i] <= final_upper[i]:
                    direction[i] = -1.0
                    supertrend[i] = final_upper[i]
                else:
                    direction[i] = 1.0
                    supertrend[i] = final_lower[i]
            else:
                if close[i] >= final_lower[i]:
                    direction[i] = 1.0
                    supertrend[i] = final_lower[i]
                else:
                    direction[i] = -1.0
                    supertrend[i] = final_upper[i]

        return {"supertrend": supertrend, "direction": direction}
