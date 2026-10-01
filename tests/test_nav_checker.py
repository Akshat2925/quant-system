"""Tests for nav_checker.py â€” no network calls, no broker."""

from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

from nav_checker import NAVChecker, ETF_LIST


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _nav_response(nav: float, days_ago: int = 0):
    """Return a mock mfapi response with the given NAV and date."""
    nav_date = (date.today() - timedelta(days=days_ago)).strftime("%d-%m-%Y")
    return MagicMock(
        status_code=200,
        json=lambda: {"data": [{"nav": str(nav), "date": nav_date}]},
        raise_for_status=lambda: None,
    )


def _checker_with_nav(nav: float, days_ago: int = 0):
    """Return a NAVChecker whose HTTP call is patched to return the given NAV."""
    checker = NAVChecker()
    with patch("nav_checker.requests.get", return_value=_nav_response(nav, days_ago)):
        yield checker


# â”€â”€ Premium band tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize("symbol", list(ETF_LIST.keys()))
def test_discount_gives_buy(symbol):
    """Market price below NAV â†’ BUY."""
    checker = NAVChecker()
    nav = 100.0
    market = 97.0  # -3% discount
    with patch("nav_checker.requests.get", return_value=_nav_response(nav)):
        result = checker.check(symbol, market)
    assert result.action == "BUY"
    assert result.premium_pct < 0
    assert result.is_good_entry


def test_within_max_premium_gives_buy():
    """Market price within max_premium of NAV â†’ BUY."""
    checker = NAVChecker()
    # CPSEETF has max_premium=0.5
    nav = 100.0
    market = 100.3   # +0.3%, within 0.5%
    with patch("nav_checker.requests.get", return_value=_nav_response(nav)):
        result = checker.check("CPSEETF-EQ", market)
    assert result.action == "BUY"


def test_above_max_premium_below_1pct_advisory_gives_limit():
    """In advisory mode: between max_premium and 1% â†’ LIMIT order."""
    checker = NAVChecker()
    nav = 100.0
    market = 100.7   # +0.7%, above CPSEETF max_premium(0.5) but below 1%
    with patch("nav_checker.requests.get", return_value=_nav_response(nav)):
        result = checker.check("CPSEETF-EQ", market, mode="advisory")
    assert result.action == "LIMIT"
    assert result.use_limit is True


def test_above_max_premium_below_1pct_strict_gives_wait():
    """In strict mode: above max_premium â†’ WAIT."""
    checker = NAVChecker()
    nav = 100.0
    market = 100.7
    with patch("nav_checker.requests.get", return_value=_nav_response(nav)):
        result = checker.check("CPSEETF-EQ", market, mode="strict")
    assert result.action == "WAIT"


def test_between_1_and_2pct_gives_wait():
    checker = NAVChecker()
    nav = 100.0
    market = 101.5   # +1.5%
    with patch("nav_checker.requests.get", return_value=_nav_response(nav)):
        result = checker.check("CPSEETF-EQ", market)
    assert result.action == "WAIT"
    assert not result.is_good_entry


def test_above_2pct_gives_skip():
    checker = NAVChecker()
    nav = 100.0
    market = 102.5   # +2.5%
    with patch("nav_checker.requests.get", return_value=_nav_response(nav)):
        result = checker.check("CPSEETF-EQ", market)
    assert result.action == "SKIP"


def test_nav_unavailable_gives_wait():
    """If mfapi.in is unreachable, action must be WAIT (fail-safe)."""
    checker = NAVChecker()
    with patch("nav_checker.requests.get", side_effect=Exception("timeout")):
        result = checker.check("CPSEETF-EQ", 100.0)
    assert result.action == "WAIT"
    assert result.nav == 0


def test_stale_nav_gives_wait():
    """NAV older than NAV_STALE_DAYS â†’ fail-safe WAIT."""
    checker = NAVChecker()
    nav = 100.0
    market = 99.0
    with patch("nav_checker.requests.get",
               return_value=_nav_response(nav, days_ago=5)):
        result = checker.check("CPSEETF-EQ", market)
    assert result.action == "WAIT"


def test_exactly_at_max_premium_boundary():
    """Market price exactly at max_premium â†’ BUY (boundary is inclusive)."""
    checker = NAVChecker()
    nav = 100.0
    market = 100.5   # exactly 0.5%, equal to CPSEETF max_premium
    with patch("nav_checker.requests.get", return_value=_nav_response(nav)):
        result = checker.check("CPSEETF-EQ", market)
    assert result.action == "BUY"


def test_nav_date_returned_in_result():
    """NAVResult must include the nav_date field."""
    checker = NAVChecker()
    with patch("nav_checker.requests.get", return_value=_nav_response(100.0)):
        result = checker.check("SETFGOLD-EQ", 99.0)
    assert result.nav_date != ""
