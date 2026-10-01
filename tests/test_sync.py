"""Tests for sync.py — all mocked."""

import time
from unittest.mock import MagicMock, patch
import pytest

from sync import SyncScheduler


def _groww(holdings=None):
    c = MagicMock()
    c.get_holdings.return_value = holdings or [
        {"trading_symbol": "CPSEETF", "quantity": 10, "average_price": 90}
    ]
    return c


def _angel(holdings=None, funds=5000.0):
    c = MagicMock()
    c.get_holdings.return_value = holdings or []
    c.get_funds.return_value = funds
    return c


# ── Sync on startup ───────────────────────────────────────────────────────────

def test_sync_all_now_calls_both_brokers():
    g = _groww()
    a = _angel()
    s = SyncScheduler(groww_connector=g, angel_connector=a)
    s.sync_all_now()
    g.get_holdings.assert_called_once()
    a.get_holdings.assert_called_once()
    a.get_funds.assert_called_once()


def test_sync_stores_holdings():
    g = _groww([{"trading_symbol": "SETFGOLD", "quantity": 3, "average_price": 120}])
    s = SyncScheduler(groww_connector=g)
    s.sync_all_now()
    assert len(s.groww_holdings) == 1
    assert s.groww_holdings[0]["trading_symbol"] == "SETFGOLD"


def test_sync_stores_angel_funds():
    a = _angel(funds=4500.0)
    s = SyncScheduler(angel_connector=a)
    s.sync_all_now()
    assert s.angel_funds == 4500.0


# ── Failure handling ──────────────────────────────────────────────────────────

def test_groww_failure_does_not_raise():
    bad = MagicMock()
    bad.get_holdings.side_effect = Exception("network down")
    s = SyncScheduler(groww_connector=bad)
    try:
        s.sync_all_now()
    except Exception as e:
        pytest.fail(f"sync raised: {e}")


def test_angel_failure_does_not_raise():
    bad = MagicMock()
    bad.get_holdings.side_effect = Exception("timeout")
    bad.get_funds.side_effect    = Exception("timeout")
    s = SyncScheduler(angel_connector=bad)
    try:
        s.sync_all_now()
    except Exception as e:
        pytest.fail(f"sync raised: {e}")


def test_groww_failure_sends_one_warning():
    bad = MagicMock()
    bad.get_holdings.side_effect = Exception("down")
    notifier = MagicMock()
    notifier._error_times = {}
    s = SyncScheduler(groww_connector=bad, notifier=notifier)
    s.sync_all_now()
    s.sync_all_now()   # second failure same day
    assert notifier.notify_error.call_count == 1


def test_angel_failure_sends_one_warning():
    bad = MagicMock()
    bad.get_holdings.side_effect = Exception("down")
    bad.get_funds.side_effect    = Exception("down")
    notifier = MagicMock()
    notifier._error_times = {}
    s = SyncScheduler(angel_connector=bad, notifier=notifier)
    s.sync_all_now()
    s.sync_all_now()
    assert notifier.notify_error.call_count == 1


# ── Interval scheduling ───────────────────────────────────────────────────────

def test_tick_does_not_sync_before_interval():
    g = _groww()
    s = SyncScheduler(groww_connector=g, groww_sync_minutes=30)
    s._last_groww_sync = time.monotonic()  # just synced
    s.tick()
    g.get_holdings.assert_not_called()


def test_tick_syncs_when_interval_elapsed():
    g = _groww()
    s = SyncScheduler(groww_connector=g, groww_sync_minutes=30)
    s._last_groww_sync = time.monotonic() - 1900  # 31+ min ago
    s.tick()
    g.get_holdings.assert_called_once()


# ── Age string ────────────────────────────────────────────────────────────────

def test_age_str_never_synced():
    assert SyncScheduler._age_str(0.0) == "never synced"


def test_age_str_recent():
    recent = time.monotonic() - 30
    age = SyncScheduler._age_str(recent)
    assert "s" in age
