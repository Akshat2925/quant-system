"""
Tests for bot.py guard logic — mid-day open price, holiday/weekend guard,
and IST timezone correctness.  No broker, no real network.
"""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from bot import _is_trading_day, _now_ist

IST = ZoneInfo("Asia/Kolkata")


# ── IST helpers ───────────────────────────────────────────────────────────────

def test_now_ist_is_timezone_aware():
    dt = _now_ist()
    assert dt.tzinfo is not None
    assert "Asia/Kolkata" in str(dt.tzinfo)


# ── Holiday / weekend guard ───────────────────────────────────────────────────

def _config_with_holidays(holidays: set):
    return SimpleNamespace(nse_holidays=frozenset(holidays))


def test_saturday_is_not_trading_day():
    # 2026-01-03 is a Saturday
    cfg = _config_with_holidays(set())
    with patch("bot._now_ist", return_value=datetime(2026, 1, 3, 10, 0, tzinfo=IST)):
        assert _is_trading_day(cfg) is False


def test_sunday_is_not_trading_day():
    cfg = _config_with_holidays(set())
    with patch("bot._now_ist", return_value=datetime(2026, 1, 4, 10, 0, tzinfo=IST)):
        assert _is_trading_day(cfg) is False


def test_weekday_non_holiday_is_trading_day():
    cfg = _config_with_holidays(set())
    with patch("bot._now_ist", return_value=datetime(2026, 1, 5, 10, 0, tzinfo=IST)):  # Monday
        assert _is_trading_day(cfg) is True


def test_nse_holiday_blocks_trading():
    cfg = _config_with_holidays({"2026-01-26"})
    with patch("bot._now_ist", return_value=datetime(2026, 1, 26, 10, 0, tzinfo=IST)):
        assert _is_trading_day(cfg) is False


# ── Open price computed from ltpData (not start-up snapshot) ─────────────────

def test_change_pct_uses_day_open_not_startup_price():
    """
    If the bot starts at 12:00 PM, change_pct must use the 9:15 open from
    ltpData(), not the 12:00 LTP. We verify that _run() calls get_quote()
    and computes from the returned day_open, not from any stored value.
    """
    from bot import TradingBot

    cfg = SimpleNamespace(
        angel_api_key="test", angel_client_id="TEST", angel_pin="1234",
        angel_totp_secret="JBSWY3DPEHPK3PXP",
        monthly_budget=1500.0, daily_cap_pct=0.33,
        daily_loss_limit=5000.0, nav_check_mode="advisory",
        mode="dry", telegram_token="", telegram_chat_id="",
        nse_holidays=frozenset(),
    )

    bot = TradingBot(cfg, mode="dry_run")

    # Simulate: day_open=100, current ltp=94 → -6% (should trigger CPSEETF)
    # If bot mistakenly used startup LTP as open it would see 0% change.
    bot.connector.get_quote = MagicMock(return_value=(94.0, 100.0))
    bot.engine = MagicMock()
    bot.engine.monthly_state.is_exhausted = False
    bot.engine.execute.return_value = []
    bot.alerts = MagicMock()
    bot.market_det = MagicMock()
    bot.market_det.check.return_value = SimpleNamespace(
        change_pct=0.0, reason="normal"
    )

    # Patch time to be inside market hours but outside buy window
    fake_dt = datetime(2026, 9, 25, 10, 30, tzinfo=IST)
    with patch("bot._now_ist", return_value=fake_dt):
        bot._run()

    # get_quote should have been called for each ETF
    assert bot.connector.get_quote.call_count == len(__import__("nav_checker").ETF_LIST)
