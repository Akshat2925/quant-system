"""Tests for tranche_engine.py — allocation math, budget caps, and the
once-per-day buy guard. These are the most important tests in the repo:
this module decides how much real money gets spent."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from tranche_engine import AllocationEngine
from nav_checker import NAVResult


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Run each test in its own empty directory so state JSON files never
    touch the real repo or leak between tests."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _good_nav_result(symbol, price):
    return NAVResult(
        symbol=symbol, market_price=price, nav=price, premium_pct=0.0,
        is_good_entry=True, use_limit=False, reason="test", action="BUY"
    )


def _mock_connector(prices: dict):
    conn = MagicMock()
    conn.get_ltp.side_effect = lambda exchange, symbol, token: prices.get(symbol)
    conn.place_buy_order.return_value = "ORDER123"
    return conn


def test_single_etf_uses_full_budget(isolated_cwd):
    engine = AllocationEngine(monthly_budget=1500.0)
    conn = _mock_connector({"CPSEETF": 100.0})

    with patch.object(engine.nav_checker, "check", side_effect=lambda sym, price: _good_nav_result(sym, price)):
        orders = engine.execute({"CPSEETF": -3.0}, conn, dry_run=True)

    assert len(orders) == 1
    assert orders[0]["symbol"] == "CPSEETF"
    # Should spend close to the full 1500 budget (minus rounding to whole units)
    assert orders[0]["amount"] <= 1500.0
    assert orders[0]["amount"] > 1400.0


def test_proportional_allocation_favors_bigger_fall(isolated_cwd):
    engine = AllocationEngine(monthly_budget=1500.0)
    conn = _mock_connector({"CPSEETF": 100.0, "SETFGOLD": 50.0})

    with patch.object(engine.nav_checker, "check", side_effect=lambda sym, price: _good_nav_result(sym, price)):
        orders = engine.execute({"CPSEETF": -6.0, "SETFGOLD": -2.0}, conn, dry_run=True)

    by_symbol = {o["symbol"]: o["amount"] for o in orders}
    # CPSEETF fell 3x as much as SETFGOLD, so should get roughly 3x the allocation
    assert by_symbol["CPSEETF"] > by_symbol["SETFGOLD"] * 2


def test_wont_buy_twice_in_one_day(isolated_cwd):
    engine = AllocationEngine(monthly_budget=1500.0)
    conn = _mock_connector({"CPSEETF": 100.0})

    with patch.object(engine.nav_checker, "check", side_effect=lambda sym, price: _good_nav_result(sym, price)):
        first = engine.execute({"CPSEETF": -3.0}, conn, dry_run=True)
        second = engine.execute({"CPSEETF": -3.0}, conn, dry_run=True)

    assert len(first) == 1
    assert len(second) == 0  # already bought today


def test_stops_when_monthly_budget_exhausted(isolated_cwd):
    engine = AllocationEngine(monthly_budget=1500.0)
    engine.monthly_state.budget_used = 1500.0  # simulate already-spent budget
    conn = _mock_connector({"CPSEETF": 100.0})

    with patch.object(engine.nav_checker, "check", side_effect=lambda sym, price: _good_nav_result(sym, price)):
        orders = engine.execute({"CPSEETF": -3.0}, conn, dry_run=True)

    assert orders == []


def test_skips_etf_when_nav_check_says_skip(isolated_cwd):
    engine = AllocationEngine(monthly_budget=1500.0)
    conn = _mock_connector({"CPSEETF": 100.0})

    bad_result = NAVResult(
        symbol="CPSEETF", market_price=100, nav=95, premium_pct=5.0,
        is_good_entry=False, use_limit=False, reason="too expensive", action="SKIP"
    )
    with patch.object(engine.nav_checker, "check", return_value=bad_result):
        orders = engine.execute({"CPSEETF": -3.0}, conn, dry_run=True)

    assert orders == []


def test_state_persists_across_engine_restarts(isolated_cwd):
    conn = _mock_connector({"CPSEETF": 100.0})

    engine1 = AllocationEngine(monthly_budget=1500.0)
    with patch.object(engine1.nav_checker, "check", side_effect=lambda sym, price: _good_nav_result(sym, price)):
        engine1.execute({"CPSEETF": -3.0}, conn, dry_run=True)

    # Simulate a restart: new engine instance should load the saved state
    engine2 = AllocationEngine(monthly_budget=1500.0)
    assert engine2.daily_state.bought_today is True
    assert engine2.monthly_state.budget_used > 0
