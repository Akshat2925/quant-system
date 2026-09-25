"""Tests for nav_checker.py — the SEBI premium/discount band logic.

Run with: pytest tests/
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from nav_checker import NAVChecker


def _check_with_nav(market_price, nav):
    checker = NAVChecker()
    with patch.object(checker, "get_nav", return_value=nav):
        return checker.check("SETFGOLD", market_price)


def test_discount_is_buy():
    result = _check_with_nav(market_price=95, nav=100)  # -5% (discount)
    assert result.action == "BUY"
    assert result.is_good_entry is True
    assert result.use_limit is False


def test_small_premium_is_buy():
    result = _check_with_nav(market_price=100.3, nav=100)  # +0.3%
    assert result.action == "BUY"
    assert result.is_good_entry is True


def test_medium_premium_uses_limit_order():
    result = _check_with_nav(market_price=100.7, nav=100)  # +0.7%
    assert result.action == "LIMIT"
    assert result.use_limit is True
    assert result.is_good_entry is True


def test_high_premium_waits():
    result = _check_with_nav(market_price=101.5, nav=100)  # +1.5%
    assert result.action == "WAIT"
    assert result.is_good_entry is False


def test_very_high_premium_skips():
    result = _check_with_nav(market_price=103, nav=100)  # +3%
    assert result.action == "SKIP"
    assert result.is_good_entry is False


def test_nav_unavailable_fails_safe():
    """Critical safety behavior: if the NAV feed is down, the bot must NOT
    default to buying blind. It should wait, not skip the check entirely."""
    result = _check_with_nav(market_price=100, nav=None)
    assert result.action == "WAIT"
    assert result.is_good_entry is False


def test_premium_boundary_exactly_half_percent_is_buy():
    result = _check_with_nav(market_price=100.5, nav=100)  # exactly +0.5%
    assert result.action == "BUY"


def test_premium_boundary_exactly_two_percent_is_skip():
    result = _check_with_nav(market_price=102, nav=100)  # exactly +2%
    assert result.action == "SKIP"
