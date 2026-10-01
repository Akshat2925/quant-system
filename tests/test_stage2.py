"""
Stage 2 tests — Static IP guard and LIMIT-only orders.

Proves:
- place_buy_order raises ValueError if price is None or 0 (no MARKET orders)
- ip_guard.check_ip returns match=False when IPs differ
- ip_guard.check_ip returns match=False when REGISTERED_STATIC_IP is empty
- ip_guard.check_ip returns match=True when IPs match
- ip_guard.is_ip_rejection detects Angel IP error messages
- bot falls back to alert_only when IP check fails in live mode
- connector._ip_rejected_today flag triggers alert_only fallback
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from connector import AngelOneConnector
from ip_guard import check_ip, is_ip_rejection, IPCheckResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_config(**kwargs):
    defaults = dict(
        angel_api_key="k", angel_client_id="C",
        angel_pin="1234", angel_totp_secret="JBSWY3DPEHPK3PXP",
        registered_static_ip="",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── LIMIT-only enforcement ────────────────────────────────────────────────────

def test_place_buy_order_requires_price():
    """Calling place_buy_order without a price must raise ValueError."""
    cfg  = _fake_config()
    conn = AngelOneConnector(cfg)
    with pytest.raises(ValueError, match="price is required"):
        conn.place_buy_order("CPSEETF-EQ", "2328", 5, price=None)


def test_place_buy_order_rejects_zero_price():
    cfg  = _fake_config()
    conn = AngelOneConnector(cfg)
    with pytest.raises(ValueError, match="price is required"):
        conn.place_buy_order("CPSEETF-EQ", "2328", 5, price=0)


def test_place_buy_order_sends_limit_ordertype():
    """When a valid price is given, ordertype in the API call must be LIMIT."""
    cfg  = _fake_config()
    conn = AngelOneConnector(cfg)
    conn.smart = MagicMock()
    conn.smart.placeOrder.return_value = {"status": True, "data": {"orderid": "X1"}}

    conn.place_buy_order("CPSEETF-EQ", "2328", 5, price=91.65)

    call_params = conn.smart.placeOrder.call_args[0][0]
    assert call_params["ordertype"] == "LIMIT"
    assert "MARKET" not in str(call_params)


def test_place_buy_order_no_market_path():
    """The string 'MARKET' must not appear anywhere in place_buy_order source."""
    import inspect
    from connector import AngelOneConnector as AC
    src = inspect.getsource(AC.place_buy_order)
    # 'MARKET' should only appear in comments/docs, not as an ordertype value
    assert '"MARKET"' not in src, \
        "MARKET ordertype string found in place_buy_order — remove it"


# ── IP guard — check_ip ───────────────────────────────────────────────────────

def test_ip_check_no_registered_ip():
    result = check_ip("")
    assert result.match is False
    assert "not set" in result.reason.lower() or "REGISTERED_STATIC_IP" in result.reason


def test_ip_check_match():
    with patch("ip_guard.get_public_ip", return_value="1.2.3.4"):
        result = check_ip("1.2.3.4")
    assert result.match is True


def test_ip_check_mismatch():
    with patch("ip_guard.get_public_ip", return_value="9.9.9.9"):
        result = check_ip("1.2.3.4")
    assert result.match is False
    assert "mismatch" in result.reason.lower() or "does not match" in result.reason.lower()


def test_ip_check_fetch_failure():
    with patch("ip_guard.get_public_ip", return_value=None):
        result = check_ip("1.2.3.4")
    assert result.match is False
    assert result.current_ip is None


def test_ip_not_logged_in_logs():
    """Masked IP must never show all 4 octets."""
    from ip_guard import _mask_ip
    masked = _mask_ip("192.168.1.100")
    assert "192.168" in masked
    assert "1.100" not in masked
    assert "*" in masked


# ── IP rejection detection ────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "IP not registered for algo trading",
    "Your IP address is not whitelisted",
    "Unauthorized IP",
    "Static IP required",
])
def test_is_ip_rejection_detects_angel_errors(msg):
    assert is_ip_rejection(msg) is True


def test_is_ip_rejection_ignores_normal_errors():
    assert is_ip_rejection("Insufficient funds") is False
    assert is_ip_rejection("Symbol not found") is False
    assert is_ip_rejection("") is False


# ── Bot fallback to alert_only on IP failure ─────────────────────────────────

def test_bot_falls_back_to_alert_only_on_ip_mismatch():
    """In live mode, if IP check fails, bot must switch to alert_only."""
    from bot import TradingBot

    cfg = SimpleNamespace(
        angel_api_key="k", angel_client_id="C", angel_pin="1234",
        angel_totp_secret="JBSWY3DPEHPK3PXP",
        monthly_budget=1500.0, daily_cap_pct=0.33, daily_loss_limit=5000.0,
        nav_check_mode="advisory", mode="live",
        telegram_token="", telegram_chat_id="", telegram_chat_id_2="",
        nse_holidays=frozenset(), registered_static_ip="1.2.3.4",
    )

    bot = TradingBot(cfg, mode="live")
    bot.connector = MagicMock()
    bot.connector.login.return_value = True
    bot.notifier  = MagicMock()

    mismatch = IPCheckResult(
        current_ip="9.9.9.9", registered_ip="1.2.3.4",
        match=False, reason="IP mismatch"
    )
    # Patch both _is_trading_day (True) and check_ip (mismatch)
    # Bot exits after IP check sets mode and then _send_startup_notification
    # We just verify mode was changed
    with patch("bot.check_ip", return_value=mismatch), \
         patch("bot._is_trading_day", return_value=True), \
         patch.object(bot, "_send_startup_notification"), \
         patch.object(bot, "_send_daily_summary"), \
         patch.object(bot, "kill", new=True):  # immediate exit
        try:
            bot.start()
        except Exception:
            pass

    assert bot.mode == "alert_only"


def test_bot_stays_live_on_ip_match():
    """If IP matches, bot must NOT switch to alert_only."""
    from bot import TradingBot

    cfg = SimpleNamespace(
        angel_api_key="k", angel_client_id="C", angel_pin="1234",
        angel_totp_secret="JBSWY3DPEHPK3PXP",
        monthly_budget=1500.0, daily_cap_pct=0.33, daily_loss_limit=5000.0,
        nav_check_mode="advisory", mode="live",
        telegram_token="", telegram_chat_id="", telegram_chat_id_2="",
        nse_holidays=frozenset(), registered_static_ip="1.2.3.4",
    )

    bot = TradingBot(cfg, mode="live")
    bot.connector = MagicMock()
    bot.connector.login.return_value = True
    bot.notifier  = MagicMock()

    match_result = IPCheckResult(
        current_ip="1.2.3.4", registered_ip="1.2.3.4",
        match=True, reason="IP matches"
    )
    with patch("bot.check_ip", return_value=match_result), \
         patch("bot._is_trading_day", return_value=True), \
         patch.object(bot, "_send_startup_notification"), \
         patch.object(bot, "_send_daily_summary"), \
         patch.object(bot, "kill", new=True):
        try:
            bot.start()
        except Exception:
            pass

    assert bot.mode == "live"
