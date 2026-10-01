"""Tests for prices.py — all mocked."""

import time
from unittest.mock import MagicMock, patch

import pytest

from prices import PriceProvider, PriceResult, PRICE_STALE_SECONDS


def _angel(ltp=100.0, open_=98.0):
    c = MagicMock()
    c.get_quote.return_value = (ltp, open_)
    return c


def _angel_fail():
    c = MagicMock()
    c.get_quote.return_value = (None, None)
    return c


def _groww_with_ltp(ltp):
    c = MagicMock()
    c.get_ltp = MagicMock(return_value=ltp)
    return c


# ── Basic fetch ───────────────────────────────────────────────────────────────

def test_get_returns_angel_price():
    pp = PriceProvider(_angel(100.0, 98.0))
    r  = pp.get("CPSEETF-EQ", "2328")
    assert r.available
    assert r.ltp == 100.0
    assert r.day_open == 98.0
    assert r.source == "angel"


def test_get_returns_none_when_angel_fails():
    pp = PriceProvider(_angel_fail())
    r  = pp.get("CPSEETF-EQ", "2328")
    assert not r.available
    assert r.source == "none"


def test_get_never_raises():
    bad = MagicMock()
    bad.get_quote.side_effect = RuntimeError("boom")
    pp = PriceProvider(bad)
    try:
        r = pp.get("CPSEETF-EQ", "2328")
        assert not r.available
    except Exception as e:
        pytest.fail(f"get() raised: {e}")


# ── Caching ───────────────────────────────────────────────────────────────────

def test_cache_returns_same_result():
    pp = PriceProvider(_angel())
    r1 = pp.get("CPSEETF-EQ", "2328")
    r2 = pp.get("CPSEETF-EQ", "2328")
    # Second call should use cache — angel only called once
    pp._angel.get_quote.assert_called_once()
    assert r1.ltp == r2.ltp


def test_invalidate_clears_cache():
    pp = PriceProvider(_angel())
    pp.get("CPSEETF-EQ", "2328")
    pp.invalidate("CPSEETF-EQ")
    pp.get("CPSEETF-EQ", "2328")
    assert pp._angel.get_quote.call_count == 2


def test_stale_price_marked():
    pp = PriceProvider(_angel())
    pp.get("CPSEETF-EQ", "2328")
    pp.mark_stale("CPSEETF-EQ")
    assert pp._cache["CPSEETF-EQ"].stale is True


# ── Groww fallback ────────────────────────────────────────────────────────────

def test_groww_fallback_when_angel_fails():
    pp = PriceProvider(_angel_fail(), groww_connector=_groww_with_ltp(95.0))
    pp._groww_live_supported = True
    r = pp.get("CPSEETF-EQ", "2328")
    assert r.ltp == 95.0
    assert r.source == "groww"
    assert r.day_open is None  # Groww doesn't provide day_open


def test_groww_not_used_if_not_supported():
    """If Groww live not supported, never call Groww for prices."""
    groww = _groww_with_ltp(95.0)
    pp = PriceProvider(_angel_fail(), groww_connector=groww)
    pp._groww_live_supported = False
    r = pp.get("CPSEETF-EQ", "2328")
    assert r.source == "none"
    groww.get_ltp.assert_not_called()


def test_detect_groww_live_disabled_quietly():
    """If Groww doesn't support live data, detect_groww_live returns False quietly."""
    groww = MagicMock()
    groww.get_ltp = MagicMock(return_value=None)
    pp = PriceProvider(_angel(), groww_connector=groww)
    result = pp.detect_groww_live()
    assert result is False
    assert pp._groww_live_supported is False


# ── Price mismatch warning ────────────────────────────────────────────────────

def test_mismatch_warning_sent_once(tmp_path):
    """Mismatch > 0.5% sends one warning, not repeated within 5 min."""
    notifier = MagicMock()

    groww = _groww_with_ltp(105.0)   # 5% diff from Angel 100
    pp = PriceProvider(_angel(100.0, 98.0), groww_connector=groww, notifier=notifier)
    pp._groww_live_supported = True
    pp._mismatch_warned = {}  # fresh state

    pp.get("CPSEETF-EQ", "2328")
    pp.invalidate("CPSEETF-EQ")
    # Second call within 5 min — should NOT warn again
    pp.get("CPSEETF-EQ", "2328")

    assert notifier.notify_error.call_count == 1


def test_no_mismatch_warning_within_threshold():
    """< 0.5% diff should not warn."""
    notifier = MagicMock()
    groww = _groww_with_ltp(100.2)   # 0.2% diff
    pp = PriceProvider(_angel(100.0, 98.0), groww_connector=groww, notifier=notifier)
    pp._groww_live_supported = True
    pp.get("CPSEETF-EQ", "2328")
    notifier.notify_error.assert_not_called()


# ── get_all() ─────────────────────────────────────────────────────────────────

def test_get_all_returns_dict():
    pp = PriceProvider(_angel())
    watchlist = [
        {"symbol": "CPSEETF-EQ",  "token": "2328",  "exchange": "NSE"},
        {"symbol": "SETFGOLD-EQ", "token": "17272", "exchange": "NSE"},
    ]
    results = pp.get_all(watchlist)
    assert "CPSEETF-EQ"  in results
    assert "SETFGOLD-EQ" in results


def test_get_all_missing_token():
    pp = PriceProvider(_angel())
    watchlist = [{"symbol": "NOTOKEN", "token": "", "exchange": "NSE"}]
    results = pp.get_all(watchlist)
    assert results["NOTOKEN"].available is False
    assert results["NOTOKEN"].reason == "no token"
