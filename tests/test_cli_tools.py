"""Tests for cli_tools.py — all mocked."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cli_tools import run_check, run_portfolio, run_reconcile


def _config(mode="alert_only"):
    return SimpleNamespace(
        mode=mode, registered_static_ip="",
        groww_api_key="", groww_api_secret="",
        monthly_budget=1500, daily_cap_pct=0.33,
    )


def _angel_ok():
    c = MagicMock()
    c.login.return_value = True
    c.get_quote.return_value = (91.65, 94.0)
    c.get_funds.return_value = 5000.0
    c.get_holdings.return_value = []
    return c


def _angel_fail():
    c = MagicMock()
    c.login.return_value = False
    c.get_quote.return_value = (None, None)
    c.get_funds.return_value = None
    c.get_holdings.return_value = []
    return c


# ── run_check ─────────────────────────────────────────────────────────────────

def test_check_returns_true_on_success(capsys):
    result = run_check(_config(), _angel_ok())
    assert result is True


def test_check_returns_false_on_login_fail(capsys):
    result = run_check(_config(), _angel_fail())
    assert result is False


def test_check_prints_pass_fail(capsys):
    run_check(_config(), _angel_ok())
    out = capsys.readouterr().out
    assert "PASS" in out


def test_check_never_raises():
    bad = MagicMock()
    bad.login.side_effect = Exception("boom")
    try:
        run_check(_config(), bad)
    except Exception as e:
        pytest.fail(f"run_check raised: {e}")


# ── run_portfolio ─────────────────────────────────────────────────────────────

def test_portfolio_never_raises(capsys):
    try:
        run_portfolio(_config(), _angel_ok())
    except Exception as e:
        pytest.fail(f"run_portfolio raised: {e}")


def test_portfolio_csv_flag(capsys):
    try:
        run_portfolio(_config(), _angel_ok(), csv_output=True)
        out = capsys.readouterr().out
        # Either has CSV header or error message — never crashes
        assert isinstance(out, str)
    except Exception as e:
        pytest.fail(f"run_portfolio csv raised: {e}")


# ── run_reconcile ─────────────────────────────────────────────────────────────

def test_reconcile_never_raises(capsys):
    try:
        run_reconcile(_config(), _angel_ok())
    except Exception as e:
        pytest.fail(f"run_reconcile raised: {e}")
