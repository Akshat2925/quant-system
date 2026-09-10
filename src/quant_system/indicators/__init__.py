"""Technical analysis indicators — single implementation each.

Import from here, never from submodules directly, to ensure there is
exactly one ATR, one RSI, etc. in the codebase.
"""

from quant_system.indicators.momentum import ROC, RSI, Stochastic, WilliamsR
from quant_system.indicators.trend import ADX, EMA, MACD, SMA, Supertrend
from quant_system.indicators.volatility import ATR, BollingerBands, HistoricalVolatility
from quant_system.indicators.volume import OBV, CMF, VWAP, VolumeSMA

__all__ = [
    # Volatility
    "ATR",
    "BollingerBands",
    "HistoricalVolatility",
    # Trend
    "SMA",
    "EMA",
    "MACD",
    "ADX",
    "Supertrend",
    # Momentum
    "RSI",
    "Stochastic",
    "ROC",
    "WilliamsR",
    # Volume
    "OBV",
    "VWAP",
    "VolumeSMA",
    "CMF",
]
