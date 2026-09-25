import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
from market_detector import MarketFallDetector


def _connector_with(ltp, candle_data=None, candle_raises=False):
    conn = MagicMock()
    conn.get_ltp.return_value = ltp
    if candle_raises:
        conn.smart.getCandleData.side_effect = Exception("API down")
    else:
        conn.smart.getCandleData.return_value = candle_data or {"status": False, "data": []}
    return conn


def test_trigger_level_3_on_major_fall():
    detector = MarketFallDetector(trigger1=-2.0, trigger2=-4.0, trigger3=-6.0)
    # open=100, ltp=93 -> -7%
    candle_data = {"status": True, "data": [[None, 100.0]]}
    conn = _connector_with(ltp=93.0, candle_data=candle_data)
    status = detector.check(conn)
    assert status.trigger_level == 3
    assert status.should_buy is True
    assert status.open_is_estimated is False


def test_no_trigger_on_small_move():
    candle_data = {"status": True, "data": [[None, 100.0]]}
    conn = _connector_with(ltp=99.5, candle_data=candle_data)  # -0.5%
    detector = MarketFallDetector()
    status = detector.check(conn)
    assert status.trigger_level == 0
    assert status.should_buy is False


def test_estimated_open_suppresses_buy_signal():
    """Safety guard: if we can't get the real opening price and have to guess,
    the bot should NOT authorize a real buy off that guess, even if the
    estimated change looks like it crossed a trigger."""
    conn = _connector_with(ltp=90.0, candle_raises=True)
    detector = MarketFallDetector(trigger1=-2.0, trigger2=-4.0, trigger3=-6.0)
    status = detector.check(conn)
    assert status.open_is_estimated is True
    assert status.should_buy is False  # suppressed despite the estimated fall
