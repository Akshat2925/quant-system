"""Momentum indicators: RSI, Stochastic, ROC, Williams %R.

Single implementation each. RSI and Stochastic are used by the Regime
Engine for momentum scoring; ROC for rate-of-change signals.
"""

from __future__ import annotations

import numpy as np

from quant_system.indicators.base import Indicator


class RSI(Indicator):
    """Relative Strength Index (Wilder smoothing).

    RSI = 100 - 100 / (1 + RS),  RS = avg_gain / avg_loss
    Uses Wilder's smoothing (same alpha as ATR) for consistency.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 2:
            raise ValueError("RSI period must be >= 2")
        self._period = period

    @property
    def name(self) -> str:
        return f"RSI({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period + 1

    def compute(self, close: np.ndarray) -> np.ndarray:
        close = np.asarray(close, dtype=float)
        n = len(close)
        rsi = np.full(n, np.nan)

        if n < self._period + 1:
            return rsi

        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # Seed: simple average of first `period` gains/losses
        avg_gain = np.mean(gains[: self._period])
        avg_loss = np.mean(losses[: self._period])

        alpha = 1.0 / self._period
        for i in range(self._period, n - 1):
            avg_gain = avg_gain * (1 - alpha) + gains[i] * alpha
            avg_loss = avg_loss * (1 - alpha) + losses[i] * alpha
            rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
            rsi[i + 1] = 100.0 - 100.0 / (1.0 + rs)

        # Fill the first valid value
        rs0 = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        rsi[self._period] = 100.0 - 100.0 / (1.0 + rs0)

        return rsi


class Stochastic(Indicator):
    """Stochastic Oscillator %K and %D.

    %K = (close - lowest_low) / (highest_high - lowest_low) * 100
    %D = SMA(%K, d_period)

    Returns dict: 'k', 'd'.
    """

    def __init__(self, k_period: int = 14, d_period: int = 3) -> None:
        self._k_period = k_period
        self._d_period = d_period

    @property
    def name(self) -> str:
        return f"Stoch({self._k_period},{self._d_period})"

    @property
    def warmup_period(self) -> int:
        return self._k_period + self._d_period - 1

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

        k = np.full(n, np.nan)
        for i in range(self._k_period - 1, n):
            hh = np.max(high[i - self._k_period + 1 : i + 1])
            ll = np.min(low[i - self._k_period + 1 : i + 1])
            if hh != ll:
                k[i] = 100.0 * (close[i] - ll) / (hh - ll)
            else:
                k[i] = 50.0  # undefined; neutral

        d = np.full(n, np.nan)
        for i in range(self._k_period + self._d_period - 2, n):
            window = k[i - self._d_period + 1 : i + 1]
            if not np.any(np.isnan(window)):
                d[i] = np.mean(window)

        return {"k": k, "d": d}


class ROC(Indicator):
    """Rate of Change: (close[i] - close[i-period]) / close[i-period] * 100."""

    def __init__(self, period: int = 10) -> None:
        self._period = period

    @property
    def name(self) -> str:
        return f"ROC({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period + 1

    def compute(self, close: np.ndarray) -> np.ndarray:
        close = np.asarray(close, dtype=float)
        n = len(close)
        out = np.full(n, np.nan)
        for i in range(self._period, n):
            prev = close[i - self._period]
            if prev != 0:
                out[i] = (close[i] - prev) / prev * 100.0
        return out


class WilliamsR(Indicator):
    """Williams %R: inverse of Stochastic %K, range [-100, 0].

    %R = (highest_high - close) / (highest_high - lowest_low) * -100
    """

    def __init__(self, period: int = 14) -> None:
        self._period = period

    @property
    def name(self) -> str:
        return f"WilliamsR({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(  # type: ignore[override]
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> np.ndarray:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        n = len(close)
        out = np.full(n, np.nan)
        for i in range(self._period - 1, n):
            hh = np.max(high[i - self._period + 1 : i + 1])
            ll = np.min(low[i - self._period + 1 : i + 1])
            if hh != ll:
                out[i] = -100.0 * (hh - close[i]) / (hh - ll)
            else:
                out[i] = -50.0
        return out
