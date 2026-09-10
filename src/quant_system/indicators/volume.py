"""Volume indicators: OBV, VWAP, Volume SMA, CMF.

Volume is a first-class signal in Indian commodity markets where delivery
squeezes and rollover pressure show up in volume long before they show in
price. These indicators are used by the Regime Engine for volume scoring.
"""

from __future__ import annotations

import numpy as np

from quant_system.indicators.base import Indicator


class OBV(Indicator):
    """On-Balance Volume.

    Cumulative volume: add when close > prev_close, subtract when < prev_close.
    OBV divergence from price is a leading signal for trend exhaustion.
    """

    @property
    def name(self) -> str:
        return "OBV"

    @property
    def warmup_period(self) -> int:
        return 1

    def compute(  # type: ignore[override]
        self,
        close: np.ndarray,
        volume: np.ndarray,
    ) -> np.ndarray:
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)
        n = len(close)
        obv = np.zeros(n)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                obv[i] = obv[i - 1] + volume[i]
            elif close[i] < close[i - 1]:
                obv[i] = obv[i - 1] - volume[i]
            else:
                obv[i] = obv[i - 1]
        return obv


class VWAP(Indicator):
    """Intraday Volume-Weighted Average Price.

    Resets at the start of each session (pass a single session's bars).
    VWAP is the execution quality benchmark — fills significantly worse
    than VWAP are flagged in the blotter.
    """

    @property
    def name(self) -> str:
        return "VWAP"

    @property
    def warmup_period(self) -> int:
        return 1

    def compute(  # type: ignore[override]
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
    ) -> np.ndarray:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)

        typical_price = (high + low + close) / 3.0
        cum_tp_vol = np.cumsum(typical_price * volume)
        cum_vol = np.cumsum(volume)
        vwap = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
        return vwap


class VolumeSMA(Indicator):
    """Simple moving average of volume. Used to detect abnormal volume spikes."""

    def __init__(self, period: int = 20) -> None:
        self._period = period

    @property
    def name(self) -> str:
        return f"VolSMA({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, volume: np.ndarray) -> np.ndarray:  # type: ignore[override]
        volume = np.asarray(volume, dtype=float)
        n = len(volume)
        out = np.full(n, np.nan)
        for i in range(self._period - 1, n):
            out[i] = np.mean(volume[i - self._period + 1 : i + 1])
        return out


class CMF(Indicator):
    """Chaikin Money Flow: measures buying/selling pressure over a window.

    CMF = sum(MFV, period) / sum(volume, period)
    where MFV = ((close - low) - (high - close)) / (high - low) * volume

    Range [-1, 1]. Positive = buying pressure, negative = selling pressure.
    """

    def __init__(self, period: int = 20) -> None:
        self._period = period

    @property
    def name(self) -> str:
        return f"CMF({self._period})"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(  # type: ignore[override]
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
    ) -> np.ndarray:
        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)
        volume = np.asarray(volume, dtype=float)
        n = len(close)

        hl_range = high - low
        mfv = np.where(
            hl_range > 0,
            ((close - low) - (high - close)) / hl_range * volume,
            0.0,
        )

        cmf = np.full(n, np.nan)
        for i in range(self._period - 1, n):
            vol_sum = np.sum(volume[i - self._period + 1 : i + 1])
            if vol_sum > 0:
                cmf[i] = np.sum(mfv[i - self._period + 1 : i + 1]) / vol_sum
        return cmf
