"""Tests for market_detector.py — no network calls."""

from unittest.mock import MagicMock

from market_detector import MarketFallDetector


def _connector(ltp=None, open_=None):
    c = MagicMock()
    if ltp is None:
        c.get_quote.return_value = (None, None)
    else:
        c.get_quote.return_value = (ltp, open_)
    return c


def test_major_fall_triggers_level3():
    det  = MarketFallDetector()
    conn = _connector(ltp=21000.0, open_=22500.0)   # -6.67%
    result = det.check(conn)
    assert result.trigger_level == 3
    assert result.should_buy is True
    assert result.change_pct < -6.0


def test_small_move_gives_level0():
    det  = MarketFallDetector()
    conn = _connector(ltp=22400.0, open_=22500.0)   # -0.44%
    result = det.check(conn)
    assert result.trigger_level == 0
    assert result.should_buy is False


def test_unavailable_quote_returns_safe_status():
    """If get_quote returns (None, None), should_buy must be False."""
    det    = MarketFallDetector()
    conn   = _connector()   # returns (None, None)
    result = det.check(conn)
    assert result.should_buy is False
    assert result.trigger_level == 0


def test_accumulation_mode_after_n_down_days():
    det = MarketFallDetector(acc_days=3)
    for _ in range(3):
        det.update_history(-1.5)
    conn   = _connector(ltp=22300.0, open_=22500.0)   # -0.89%
    result = det.check(conn)
    assert result.accumulation is True


def test_no_accumulation_with_mixed_history():
    det = MarketFallDetector(acc_days=3)
    det.update_history(-1.0)
    det.update_history(+0.5)
    det.update_history(-1.0)
    conn   = _connector(ltp=22300.0, open_=22500.0)
    result = det.check(conn)
    assert result.accumulation is False


def test_uses_get_quote_not_candle():
    """Detector must call get_quote(), not smart.getCandleData()."""
    det  = MarketFallDetector()
    conn = MagicMock()
    conn.get_quote.return_value = (22500.0, 22500.0)
    det.check(conn)
    conn.get_quote.assert_called_once()
    # getCandleData should never be called
    assert not hasattr(conn, "smart") or not conn.smart.getCandleData.called
