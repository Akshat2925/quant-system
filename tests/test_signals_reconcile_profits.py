"""Tests for signals.py, reconcile.py, profit_alerts.py."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from signals import compute, BuySignal
from reconcile import diff, handle_manual_bought, SNAPSHOT_FILE
from profit_alerts import check, DEFAULT_LEVELS


# ── signals.compute ───────────────────────────────────────────────────────────

def test_compute_returns_signal():
    sig = compute(
        symbol="CPSEETF-EQ", change_pct=-2.5, trigger_pct=-2.0,
        day_open=94.0, ltp=91.65, allocation=495.0,
        portfolio_item=None, total_portfolio_value=None,
        budget_remaining=1005.0, daily_cap=495.0,
        nav_note="NAV ok", funds_available=5000.0,
    )
    assert sig is not None
    assert isinstance(sig, BuySignal)
    assert sig.symbol == "CPSEETF-EQ"
    assert sig.suggested_qty == int(495.0 / 91.65)
    assert sig.limit_price == round(91.65 * 1.001, 2)


def test_compute_zero_qty_returns_none():
    sig = compute(
        symbol="CPSEETF-EQ", change_pct=-2.5, trigger_pct=-2.0,
        day_open=94.0, ltp=91.65, allocation=10.0,  # too small for 1 unit
        portfolio_item=None, total_portfolio_value=None,
        budget_remaining=10.0, daily_cap=10.0,
        nav_note="ok", funds_available=5000.0,
    )
    assert sig is None


def test_compute_new_avg_price():
    item = SimpleNamespace(qty_total=10, avg_price=90.0, invested=900.0,
                           qty_groww=10, qty_angel=0, current_price=92.0)
    sig = compute(
        symbol="CPSEETF-EQ", change_pct=-2.5, trigger_pct=-2.0,
        day_open=94.0, ltp=91.65, allocation=495.0,
        portfolio_item=item, total_portfolio_value=1000.0,
        budget_remaining=1005.0, daily_cap=495.0,
        nav_note="ok", funds_available=5000.0,
    )
    assert sig is not None
    qty_new = int(495.0 / 91.65)
    expected_new_avg = (90.0 * 10 + 91.65 * qty_new) / (10 + qty_new)
    assert abs(sig.new_avg_price - expected_new_avg) < 0.05


def test_compute_funds_insufficient():
    sig = compute(
        symbol="CPSEETF-EQ", change_pct=-2.5, trigger_pct=-2.0,
        day_open=94.0, ltp=91.65, allocation=495.0,
        portfolio_item=None, total_portfolio_value=None,
        budget_remaining=495.0, daily_cap=495.0,
        nav_note="ok", funds_available=50.0,  # way too low
        buy_broker="angel",
    )
    assert sig is not None
    assert sig.funds_ok is False
    assert sig.funds_shortfall > 0
    assert "Insufficient" in sig.message


def test_compute_message_contains_key_info():
    sig = compute(
        symbol="CPSEETF-EQ", change_pct=-2.5, trigger_pct=-2.0,
        day_open=94.0, ltp=91.65, allocation=495.0,
        portfolio_item=None, total_portfolio_value=None,
        budget_remaining=1005.0, daily_cap=495.0,
        nav_note="NAV ok", funds_available=5000.0,
    )
    assert "CPSEETF-EQ" in sig.message
    assert "no order was placed" in sig.message
    assert "LIMIT" in sig.message or "limit" in sig.message.lower()


def test_compute_never_raises():
    try:
        compute("X", 0, 0, 0, 0, 0, None, None, 0, 0, "", None)
    except Exception as e:
        pytest.fail(f"compute raised: {e}")


def test_compute_alert_only_tag_in_message():
    sig = compute(
        symbol="CPSEETF-EQ", change_pct=-2.5, trigger_pct=-2.0,
        day_open=94.0, ltp=91.65, allocation=495.0,
        portfolio_item=None, total_portfolio_value=None,
        budget_remaining=495.0, daily_cap=495.0,
        nav_note="ok", funds_available=5000.0,
        mode="alert_only",
    )
    assert "ALERT ONLY" in sig.message


# ── reconcile.diff ────────────────────────────────────────────────────────────

def test_first_snapshot_returns_empty(tmp_path):
    holdings = [{"symbol": "CPSEETF-EQ", "qty_total": 10, "avg_price": 90}]
    with patch("reconcile.SNAPSHOT_FILE", str(tmp_path / "snap.json")):
        events = diff(holdings)
    assert events == []


def test_purchase_detected(tmp_path):
    snap_path = str(tmp_path / "snap.json")
    # Write an existing snapshot
    with open(snap_path, "w") as f:
        json.dump({
            "first": False,
            "holdings": {"CPSEETF-EQ": {"quantity": 5, "avg_price": 90}},
        }, f)

    holdings = [{"symbol": "CPSEETF-EQ", "qty_total": 10, "avg_price": 90, "source": "groww"}]
    with patch("reconcile.SNAPSHOT_FILE", snap_path):
        events = diff(holdings)

    assert len(events) == 1
    assert events[0]["type"] == "purchase"
    assert events[0]["qty_change"] == 5.0


def test_sale_detected(tmp_path):
    snap_path = str(tmp_path / "snap.json")
    with open(snap_path, "w") as f:
        json.dump({
            "first": False,
            "holdings": {"CPSEETF-EQ": {"quantity": 10, "avg_price": 90}},
        }, f)

    holdings = [{"symbol": "CPSEETF-EQ", "qty_total": 7, "avg_price": 90, "source": "groww"}]
    with patch("reconcile.SNAPSHOT_FILE", snap_path):
        events = diff(holdings)

    assert len(events) == 1
    assert events[0]["type"] == "sale"


def test_no_change_no_events(tmp_path):
    snap_path = str(tmp_path / "snap.json")
    with open(snap_path, "w") as f:
        json.dump({
            "first": False,
            "holdings": {"CPSEETF-EQ": {"quantity": 10, "avg_price": 90}},
        }, f)

    holdings = [{"symbol": "CPSEETF-EQ", "qty_total": 10, "avg_price": 90, "source": "groww"}]
    with patch("reconcile.SNAPSHOT_FILE", snap_path):
        events = diff(holdings)
    assert events == []


def test_diff_never_raises(tmp_path):
    with patch("reconcile.SNAPSHOT_FILE", str(tmp_path / "snap.json")):
        try:
            diff([{"bad": "data"}])
        except Exception as e:
            pytest.fail(f"diff raised: {e}")


def test_manual_bought():
    event = handle_manual_bought("CPSEETF-EQ", 5, 91.65)
    assert event["type"] == "purchase"
    assert event["symbol"] == "CPSEETF-EQ"
    assert event["qty_change"] == 5
    assert abs(event["est_amount"] - 5 * 91.65) < 0.01


# ── profit_alerts.check ───────────────────────────────────────────────────────

def _item(symbol, avg_price, ltp, qty_groww=10, qty_angel=0):
    return SimpleNamespace(
        symbol=symbol, avg_price=avg_price, current_price=ltp,
        qty_groww=qty_groww, qty_angel=qty_angel,
        qty_total=qty_groww + qty_angel,
    )


def test_profit_alert_fires_at_threshold(tmp_path):
    with patch("profit_alerts.ALERT_STATE_FILE", str(tmp_path / "pa.json")):
        fired = check([_item("CPSEETF-EQ", 100.0, 110.0)], levels=[8.0])
    assert len(fired) == 1
    assert fired[0]["symbol"] == "CPSEETF-EQ"
    assert fired[0]["level"] == 8.0


def test_profit_alert_not_fired_below_threshold(tmp_path):
    with patch("profit_alerts.ALERT_STATE_FILE", str(tmp_path / "pa.json")):
        fired = check([_item("CPSEETF-EQ", 100.0, 105.0)], levels=[8.0])
    assert fired == []


def test_profit_alert_not_fired_twice(tmp_path):
    state_path = str(tmp_path / "pa.json")
    with patch("profit_alerts.ALERT_STATE_FILE", state_path):
        fired1 = check([_item("CPSEETF-EQ", 100.0, 110.0)], levels=[8.0])
        fired2 = check([_item("CPSEETF-EQ", 100.0, 112.0)], levels=[8.0])
    assert len(fired1) == 1
    assert len(fired2) == 0   # already fired


def test_profit_alert_rearms_after_price_drops(tmp_path):
    state_path = str(tmp_path / "pa.json")
    with patch("profit_alerts.ALERT_STATE_FILE", state_path):
        check([_item("CPSEETF-EQ", 100.0, 110.0)], levels=[8.0])   # fire
        check([_item("CPSEETF-EQ", 100.0, 103.0)], levels=[8.0])   # drop below 8-2=6%
        fired = check([_item("CPSEETF-EQ", 100.0, 110.0)], levels=[8.0])  # should re-fire
    assert len(fired) == 1


def test_profit_alert_message_has_per_broker_qty(tmp_path):
    notifier = MagicMock()
    item = _item("CPSEETF-EQ", 100.0, 115.0, qty_groww=8, qty_angel=3)
    with patch("profit_alerts.ALERT_STATE_FILE", str(tmp_path / "pa.json")):
        check([item], notifier=notifier, levels=[8.0])
    call_args = notifier.notify.call_args
    msg = call_args[0][1]
    assert "Groww" in msg or "Angel" in msg


def test_profit_alert_never_raises(tmp_path):
    with patch("profit_alerts.ALERT_STATE_FILE", str(tmp_path / "pa.json")):
        try:
            check([SimpleNamespace(symbol="X", avg_price=0, current_price=None,
                                   qty_groww=0, qty_angel=0)])
        except Exception as e:
            pytest.fail(f"check raised: {e}")
