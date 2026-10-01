"""
Tests for tranche_engine.py.

All tests use a tmp_path fixture to isolate state files so no test touches
another's files.  No network calls, no real broker.
"""

import json
import os
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tranche_engine import (
    AllocationEngine,
    TrancheState,
    MonthlyState,
    compute_allocations,
    _atomic_write_json,
)
from nav_checker import ETF_LIST


# â”€â”€ Fixtures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _fake_config(tmp_path, monthly_budget=1500.0, daily_cap_pct=0.33,
                 daily_loss_limit=5000.0):
    return SimpleNamespace(
        monthly_budget   = monthly_budget,
        daily_cap_pct    = daily_cap_pct,
        daily_loss_limit = daily_loss_limit,
        nav_check_mode   = "advisory",
    )


def _fake_connector(ltp=100.0):
    c = MagicMock()
    c.get_ltp.return_value = ltp
    c.place_buy_order.return_value = "ORDER123"
    c.get_order_status.return_value = "complete"
    return c


def _good_nav_result():
    from nav_checker import NAVResult
    return NAVResult(
        symbol="CPSEETF-EQ", market_price=100.0, nav=100.0,
        nav_date="2026-01-01", premium_pct=0.0,
        is_good_entry=True, use_limit=False,
        reason="âœ… Discount", action="BUY",
    )


def _make_engine(tmp_path, config=None, dry_run=True, mode="dry_run"):
    cfg = config or _fake_config(tmp_path)
    eng = AllocationEngine(config=cfg, dry_run=dry_run, mode=mode)
    # Redirect state files into tmp_path
    suffix = ".dry.json" if dry_run else ".json"
    eng._state_file   = str(tmp_path / f"tranche_state{suffix}")
    eng._monthly_file = str(tmp_path / f"monthly_budget{suffix}")
    eng.daily_state   = TrancheState(date=str(date.today()))
    eng.monthly_state = MonthlyState(
        month=str(date.today())[:7],
        budget_used=0.0,
        budget_total=cfg.monthly_budget,
    )
    return eng


# â”€â”€ compute_allocations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_single_etf_gets_full_remaining():
    allocs = compute_allocations(
        {"CPSEETF-EQ": -3.0}, remaining=1500.0,
        daily_cap=495.0, bought_symbols=[]
    )
    assert "CPSEETF-EQ" in allocs
    assert abs(allocs["CPSEETF-EQ"] - 495.0) < 0.02


def test_proportional_allocation_two_etfs():
    # CPSEETF -6%, SETFGOLD -2%  â†’  CPSEETF 75%, SETFGOLD 25%
    allocs = compute_allocations(
        {"CPSEETF-EQ": -6.0, "SETFGOLD-EQ": -2.0},
        remaining=1500.0, daily_cap=1500.0, bought_symbols=[]
    )
    total = sum(allocs.values())
    assert abs(total - 1500.0) < 0.02
    assert allocs["CPSEETF-EQ"] > allocs["SETFGOLD-EQ"]
    assert abs(allocs["CPSEETF-EQ"] / total - 0.75) < 0.01


def test_daily_cap_limits_spend():
    # daily_cap = 33% of 1500 = 495
    allocs = compute_allocations(
        {"CPSEETF-EQ": -3.0, "SETFGOLD-EQ": -2.0},
        remaining=1500.0, daily_cap=495.0, bought_symbols=[]
    )
    assert sum(allocs.values()) <= 495.01


def test_bought_symbols_excluded():
    allocs = compute_allocations(
        {"CPSEETF-EQ": -3.0, "SETFGOLD-EQ": -2.0},
        remaining=1500.0, daily_cap=1500.0,
        bought_symbols=["CPSEETF-EQ"]
    )
    assert "CPSEETF-EQ" not in allocs
    assert "SETFGOLD-EQ" in allocs


def test_silver_fixed_allocation():
    allocs = compute_allocations(
        {"SILVERIETF-EQ": -7.0},
        remaining=1500.0, daily_cap=1500.0, bought_symbols=[]
    )
    from tranche_engine import SILVER_FIXED
    assert abs(allocs.get("SILVERIETF-EQ", 0) - SILVER_FIXED) < 0.01


def test_silver_not_triggered_below_threshold():
    """Silver at -5% (above -6.5 threshold) should not be allocated."""
    allocs = compute_allocations(
        {"SILVERIETF-EQ": -5.0},
        remaining=1500.0, daily_cap=1500.0, bought_symbols=[]
    )
    assert allocs.get("SILVERIETF-EQ", 0) == 0


# â”€â”€ Engine.execute â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_execute_places_order_and_charges_budget(tmp_path):
    eng  = _make_engine(tmp_path, dry_run=False)
    conn = _fake_connector(ltp=100.0)

    with patch.object(eng.nav_checker, "check", return_value=_good_nav_result()):
        orders, skips = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=5000.0)

    assert len(orders) == 1
    assert orders[0]["symbol"] == "CPSEETF-EQ"
    assert eng.monthly_state.budget_used > 0


def test_bought_today_blocks_second_execute(tmp_path):
    eng       = _make_engine(tmp_path, dry_run=False)
    eng.daily_state.bought_today = True
    eng._save_daily()

    conn   = _fake_connector()
    orders, skips = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=5000.0)
    assert orders == []


def test_monthly_budget_exhaustion_blocks_buy(tmp_path):
    cfg = _fake_config(tmp_path, monthly_budget=1500.0)
    eng = _make_engine(tmp_path, config=cfg, dry_run=False)
    eng.monthly_state.budget_used = 1500.0  # exhausted
    eng._save_monthly()

    conn   = _fake_connector()
    orders, skips = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=5000.0)
    assert orders == []


def test_nav_skip_does_not_charge_budget(tmp_path):
    from nav_checker import NAVResult
    skip_result = NAVResult(
        symbol="CPSEETF-EQ", market_price=100.0, nav=100.0,
        nav_date="2026-01-01", premium_pct=2.5,
        is_good_entry=False, use_limit=False,
        reason="ðŸš« Skip", action="SKIP",
    )
    eng  = _make_engine(tmp_path, dry_run=False)
    conn = _fake_connector()
    with patch.object(eng.nav_checker, "check", return_value=skip_result):
        orders, skips = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=5000.0)

    assert orders == []
    assert eng.monthly_state.budget_used == 0.0


def test_state_saved_after_each_order(tmp_path):
    """State file must exist after the first successful order."""
    eng  = _make_engine(tmp_path, dry_run=False)
    conn = _fake_connector(ltp=100.0)

    with patch.object(eng.nav_checker, "check", return_value=_good_nav_result()):
        eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=5000.0)

    assert Path(eng._state_file).exists()
    with open(eng._state_file) as f:
        state = json.load(f)
    assert "CPSEETF-EQ" in state["bought_symbols"]


def test_crash_after_first_order_no_double_buy(tmp_path):
    """Simulate a crash after CPSEETF is bought but before SETFGOLD.
    On restart, CPSEETF should be in bought_symbols and not re-bought."""
    eng  = _make_engine(tmp_path, dry_run=False)
    conn = _fake_connector(ltp=100.0)

    # First run â€” buys CPSEETF, then raises mid-loop for SETFGOLD
    call_count = {"n": 0}
    good        = _good_nav_result()

    def nav_side_effect(symbol, price, mode="advisory"):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated crash")
        return good

    with patch.object(eng.nav_checker, "check", side_effect=nav_side_effect):
        eng.execute({"CPSEETF-EQ": -3.0, "SETFGOLD-EQ": -2.0}, conn,
                    funds_available=5000.0)

    # Reload engine from persisted state
    eng2  = _make_engine(tmp_path, dry_run=False)
    eng2._state_file   = eng._state_file
    eng2._monthly_file = eng._monthly_file
    eng2.daily_state   = eng2._load_daily()
    eng2.monthly_state = eng2._load_monthly()

    assert "CPSEETF-EQ" in eng2.daily_state.bought_symbols

    # Second execute â€” CPSEETF must NOT be re-bought
    conn2 = _fake_connector(ltp=100.0)
    with patch.object(eng2.nav_checker, "check", return_value=good):
        orders2, _ = eng2.execute({"CPSEETF-EQ": -3.0, "SETFGOLD-EQ": -2.0},
                               conn2, funds_available=5000.0)

    bought = [o["symbol"] for o in orders2]
    assert "CPSEETF-EQ" not in bought


def test_dry_run_does_not_touch_live_files(tmp_path):
    """Dry-run state files must be completely separate from live state files."""
    dry_eng  = _make_engine(tmp_path, dry_run=True)
    live_eng = _make_engine(tmp_path, dry_run=False)

    assert dry_eng._state_file   != live_eng._state_file
    assert dry_eng._monthly_file != live_eng._monthly_file

    conn = _fake_connector(ltp=100.0)
    with patch.object(dry_eng.nav_checker, "check",
                      return_value=_good_nav_result()):
        dry_eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=None)

    # Live state file must not exist
    assert not Path(live_eng._state_file).exists()


def test_month_rollover_resets_budget(tmp_path):
    cfg = _fake_config(tmp_path, monthly_budget=1500.0)
    eng = _make_engine(tmp_path, config=cfg, dry_run=True)

    # Simulate previous month with some spend
    eng.monthly_state = MonthlyState(
        month="2026-08",   # old month
        budget_used=900.0,
        budget_total=1500.0,
    )
    eng._save_monthly()

    # Refresh triggers rollover to today's month
    eng._refresh_state()

    assert eng.monthly_state.month == str(date.today())[:7]
    assert eng.monthly_state.budget_used == 0.0


def test_rejected_order_not_charged(tmp_path):
    """An order that is rejected must not increase budget_used."""
    eng  = _make_engine(tmp_path, dry_run=False)
    conn = _fake_connector(ltp=100.0)
    conn.get_order_status.return_value = "rejected"

    with patch.object(eng.nav_checker, "check", return_value=_good_nav_result()):
        orders, skips = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=5000.0)

    assert orders == []
    assert eng.monthly_state.budget_used == 0.0


def test_insufficient_funds_skips_order(tmp_path):
    eng  = _make_engine(tmp_path, dry_run=False)
    conn = _fake_connector(ltp=500.0)   # 4 units = â‚¹2000, funds = â‚¹100

    with patch.object(eng.nav_checker, "check", return_value=_good_nav_result()):
        orders, skips = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=100.0)

    assert orders == []


def test_quantity_rounding_carryback(tmp_path):
    """Unspent amount from rounding must not be counted as budget_used."""
    # ltp=219, silver alloc=350 â†’ qty=1 â†’ actual_cost=219 â†’ unspent=131
    eng  = _make_engine(tmp_path, dry_run=False)
    conn = _fake_connector(ltp=219.0)

    from nav_checker import NAVResult
    silver_nav = NAVResult(
        symbol="SILVERIETF-EQ", market_price=219.0, nav=215.0,
        nav_date="2026-01-01", premium_pct=1.86,
        is_good_entry=False, use_limit=False,
        reason="WAIT", action="WAIT",
    )

    good = _good_nav_result()

    def nav_side(symbol, price, mode="advisory"):
        if symbol == "SILVERIETF-EQ":
            return silver_nav
        return good

    with patch.object(eng.nav_checker, "check", side_effect=nav_side):
        orders, skips = eng.execute(
            {"SILVERIETF-EQ": -7.0, "CPSEETF-EQ": -3.0},
            conn, funds_available=5000.0,
        )

    # Silver was WAITed â€” budget should only reflect CPSEETF
    symbols = [o["symbol"] for o in orders]
    assert "SILVERIETF-EQ" not in symbols


def test_atomic_write_json(tmp_path):
    """_atomic_write_json must produce valid JSON and not leave temp files."""
    target = str(tmp_path / "test.json")
    _atomic_write_json(target, {"key": "value", "num": 42})

    with open(target) as f:
        data = json.load(f)
    assert data == {"key": "value", "num": 42}

    # No leftover .tmp_ files
    tmp_files = list(tmp_path.glob(".tmp_*"))
    assert tmp_files == []
