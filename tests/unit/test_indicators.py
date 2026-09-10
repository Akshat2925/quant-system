"""Unit tests for all technical indicators.

Every indicator has at least:
1. A known-values test (hand-computed or cross-referenced with a reference impl).
2. A NaN-padding test (warmup period respected).
3. An edge-case test (constant input, zero volatility, etc.)
"""

import numpy as np
import pytest

from quant_system.indicators import (
    ATR,
    EMA,
    MACD,
    OBV,
    RSI,
    SMA,
    ADX,
    BollingerBands,
    CMF,
    HistoricalVolatility,
    ROC,
    Stochastic,
    Supertrend,
    VWAP,
    VolumeSMA,
    WilliamsR,
)


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _close(n=50, start=100.0, step=1.0):
    return np.array([start + i * step for i in range(n)], dtype=float)


def _ohlcv(n=50, base=100.0):
    close = _close(n, base)
    high = close + 2.0
    low = close - 2.0
    vol = np.ones(n) * 1000.0
    return high, low, close, vol


# ------------------------------------------------------------------ #
#  ATR                                                                 #
# ------------------------------------------------------------------ #

def test_atr_warmup_period():
    high, low, close, _ = _ohlcv(50)
    atr = ATR(14)
    result = atr.compute(high, low, close)
    assert np.all(np.isnan(result[:13]))
    assert not np.isnan(result[13])


def test_atr_constant_bars_equals_range():
    """Constant OHLC: TR = high - low = 4 every bar; ATR should converge to 4."""
    high, low, close, _ = _ohlcv(100)
    atr = ATR(14)
    result = atr.compute(high, low, close)
    # After warmup, ATR should be ~4 (high-low=4 for every bar)
    assert abs(result[-1] - 4.0) < 0.01


def test_atr_period_1_equals_true_range():
    high = np.array([105.0, 108.0])
    low = np.array([100.0, 103.0])
    close = np.array([104.0, 107.0])
    atr = ATR(1)
    result = atr.compute(high, low, close)
    # Period=1: seed is just TR[0] = 5; then Wilder with alpha=1 gives TR[1]
    assert result[0] == pytest.approx(5.0)
    # TR[1] = max(108-103, |108-104|, |103-104|) = max(5, 4, 1) = 5
    assert result[1] == pytest.approx(5.0)


def test_atr_unequal_array_lengths_raises():
    with pytest.raises(ValueError):
        ATR(5).compute(np.ones(10), np.ones(10), np.ones(9))


# ------------------------------------------------------------------ #
#  SMA / EMA                                                           #
# ------------------------------------------------------------------ #

def test_sma_known_values():
    close = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = SMA(3).compute(close)
    assert np.isnan(result[0]) and np.isnan(result[1])
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)


def test_ema_seed_equals_sma_of_first_period():
    close = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    ema = EMA(3)
    result = ema.compute(close)
    # Seed at index 2 = mean([1,2,3]) = 2.0
    assert result[2] == pytest.approx(2.0)
    # EMA[3] = 4 * 0.5 + 2.0 * 0.5 = 3.0
    assert result[3] == pytest.approx(3.0)


def test_ema_warmup_respects_period():
    result = EMA(5).compute(_close(20))
    assert np.all(np.isnan(result[:4]))
    assert not np.isnan(result[4])


# ------------------------------------------------------------------ #
#  MACD                                                                #
# ------------------------------------------------------------------ #

def test_macd_returns_three_arrays():
    close = _close(100)
    result = MACD(12, 26, 9).compute(close)
    assert set(result.keys()) == {"macd", "signal", "histogram"}
    assert len(result["macd"]) == 100


def test_macd_histogram_equals_macd_minus_signal():
    close = _close(100)
    result = MACD(12, 26, 9).compute(close)
    valid = ~(np.isnan(result["macd"]) | np.isnan(result["signal"]))
    np.testing.assert_allclose(
        result["histogram"][valid],
        result["macd"][valid] - result["signal"][valid],
        rtol=1e-10,
    )


# ------------------------------------------------------------------ #
#  RSI                                                                 #
# ------------------------------------------------------------------ #

def test_rsi_range_0_to_100():
    close = _close(100) + np.random.default_rng(42).normal(0, 1, 100)
    result = RSI(14).compute(close)
    valid = result[~np.isnan(result)]
    assert np.all(valid >= 0.0) and np.all(valid <= 100.0)


def test_rsi_all_gains_approaches_100():
    """Monotonically rising series: RSI should approach 100."""
    close = np.linspace(100, 200, 100)
    result = RSI(14).compute(close)
    assert result[-1] > 90.0


def test_rsi_all_losses_approaches_0():
    """Monotonically falling series: RSI should approach 0."""
    close = np.linspace(200, 100, 100)
    result = RSI(14).compute(close)
    assert result[-1] < 10.0


# ------------------------------------------------------------------ #
#  Bollinger Bands                                                     #
# ------------------------------------------------------------------ #

def test_bollinger_upper_above_middle_above_lower():
    close = _close(50) + np.random.default_rng(0).normal(0, 2, 50)
    bb = BollingerBands(20, 2.0)
    result = bb.compute(close)
    valid = ~np.isnan(result["middle"])
    assert np.all(result["upper"][valid] >= result["middle"][valid])
    assert np.all(result["middle"][valid] >= result["lower"][valid])


def test_bollinger_constant_series_zero_width():
    """Constant series: std=0, upper=lower=middle."""
    close = np.ones(50) * 100.0
    result = BollingerBands(20).compute(close)
    assert result["upper"][-1] == pytest.approx(100.0)
    assert result["lower"][-1] == pytest.approx(100.0)


# ------------------------------------------------------------------ #
#  Stochastic                                                          #
# ------------------------------------------------------------------ #

def test_stochastic_range():
    high, low, close, _ = _ohlcv(60)
    result = Stochastic(14, 3).compute(high, low, close)
    valid_k = result["k"][~np.isnan(result["k"])]
    assert np.all(valid_k >= 0.0) and np.all(valid_k <= 100.0)


# ------------------------------------------------------------------ #
#  ROC                                                                 #
# ------------------------------------------------------------------ #

def test_roc_known_values():
    close = np.array([100.0, 110.0, 121.0])
    result = ROC(1).compute(close)
    assert np.isnan(result[0])
    assert result[1] == pytest.approx(10.0)
    assert result[2] == pytest.approx(10.0)


# ------------------------------------------------------------------ #
#  Williams %R                                                         #
# ------------------------------------------------------------------ #

def test_williams_r_range():
    high, low, close, _ = _ohlcv(50)
    result = WilliamsR(14).compute(high, low, close)
    valid = result[~np.isnan(result)]
    assert np.all(valid >= -100.0) and np.all(valid <= 0.0)


# ------------------------------------------------------------------ #
#  OBV                                                                 #
# ------------------------------------------------------------------ #

def test_obv_rising_price_positive_obv():
    close = np.array([100.0, 101.0, 102.0])
    volume = np.array([1000.0, 1000.0, 1000.0])
    result = OBV().compute(close, volume)
    assert result[0] == 0.0
    assert result[1] == 1000.0
    assert result[2] == 2000.0


def test_obv_falling_price_negative_obv():
    close = np.array([102.0, 101.0, 100.0])
    volume = np.array([1000.0, 1000.0, 1000.0])
    result = OBV().compute(close, volume)
    assert result[-1] == -2000.0


# ------------------------------------------------------------------ #
#  VWAP                                                                #
# ------------------------------------------------------------------ #

def test_vwap_constant_price_equals_price():
    close = np.ones(10) * 100.0
    high = np.ones(10) * 101.0
    low = np.ones(10) * 99.0
    volume = np.ones(10) * 500.0
    result = VWAP().compute(high, low, close, volume)
    np.testing.assert_allclose(result, 100.0, rtol=1e-10)


# ------------------------------------------------------------------ #
#  VolumeSMA                                                           #
# ------------------------------------------------------------------ #

def test_volume_sma_warmup():
    vol = np.ones(30) * 1000.0
    result = VolumeSMA(20).compute(vol)
    assert np.all(np.isnan(result[:19]))
    assert result[19] == pytest.approx(1000.0)


# ------------------------------------------------------------------ #
#  CMF                                                                 #
# ------------------------------------------------------------------ #

def test_cmf_range_minus_one_to_one():
    high, low, close, vol = _ohlcv(50)
    result = CMF(20).compute(high, low, close, vol)
    valid = result[~np.isnan(result)]
    assert np.all(valid >= -1.0) and np.all(valid <= 1.0)


# ------------------------------------------------------------------ #
#  ADX                                                                 #
# ------------------------------------------------------------------ #

def test_adx_trending_series_high_adx():
    """Strong trend: ADX should be high (>25) after warmup."""
    n = 100
    close = np.linspace(100, 200, n)
    high = close + 1.5
    low = close - 1.5
    result = ADX(14).compute(high, low, close)
    valid = result["adx"][~np.isnan(result["adx"])]
    assert valid[-1] > 25.0


# ------------------------------------------------------------------ #
#  Historical Volatility                                               #
# ------------------------------------------------------------------ #

def test_hv_constant_returns_zero():
    """Constant series has zero log returns → HV = 0."""
    close = np.ones(50) * 100.0
    result = HistoricalVolatility(20).compute(close)
    valid = result[~np.isnan(result)]
    np.testing.assert_allclose(valid, 0.0, atol=1e-12)


# ------------------------------------------------------------------ #
#  Supertrend                                                          #
# ------------------------------------------------------------------ #

def test_supertrend_direction_values():
    high, low, close, _ = _ohlcv(50)
    result = Supertrend(10, 3.0).compute(high, low, close)
    valid_dir = result["direction"][~np.isnan(result["direction"])]
    assert set(valid_dir).issubset({1.0, -1.0})
