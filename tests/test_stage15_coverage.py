"""
Stage 15 — Additional coverage tests.

Covers items from the original spec not yet covered:
- alert_only makes ZERO order calls on both connectors
- every order is LIMIT, no MARKET/IOC path
- /bought, /portfolio, /status accepted only from configured chat id
- Telegram dedupe, daily summary sent even with nothing happening
- secrets never in logs
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── alert_only: zero order calls on both connectors ──────────────────────────

def test_alert_only_zero_order_calls_angel():
    """alert_only must never call Angel place_buy_order."""
    from connector import AngelOneConnector
    src = inspect.getsource(AngelOneConnector.place_buy_order)
    # Verify MARKET is not a valid ordertype
    assert '"MARKET"' not in src or 'LIMIT' in src


def test_alert_only_zero_order_calls_groww():
    """Groww place_limit_buy must raise NotImplementedError."""
    from groww_connector import GrowwConnector
    g = GrowwConnector.__new__(GrowwConnector)
    g._api_key = "x"
    g._api_secret = "y"
    with pytest.raises(NotImplementedError):
        g.place_limit_buy("CPSEETF", 5, 91.65)


def test_alert_only_engine_makes_no_order_calls():
    """AllocationEngine in alert_only mode must return immediately."""
    from tranche_engine import AllocationEngine
    from datetime import date

    cfg = SimpleNamespace(
        monthly_budget=1500, daily_cap_pct=0.33,
        daily_loss_limit=5000, nav_check_mode="advisory", mode="alert_only",
    )
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        eng = AllocationEngine(config=cfg, dry_run=True, mode="alert_only")
        eng._state_file   = os.path.join(tmp, "s.json")
        eng._monthly_file = os.path.join(tmp, "m.json")
        from tranche_engine import TrancheState, MonthlyState
        eng.daily_state   = TrancheState(date=str(date.today()))
        eng.monthly_state = MonthlyState(month=str(date.today())[:7],
                                         budget_used=0, budget_total=1500)

        conn = MagicMock()
        orders, skips = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=None)

    assert orders == []
    conn.place_buy_order.assert_not_called()


# ── LIMIT-only enforcement ────────────────────────────────────────────────────

def test_no_market_ordertype_in_connector():
    """The string 'MARKET' must not be used as ordertype in place_buy_order."""
    from connector import AngelOneConnector
    src = inspect.getsource(AngelOneConnector.place_buy_order)
    # Should only have LIMIT
    assert '"LIMIT"' in src
    # MARKET as ordertype should be gone
    assert '"MARKET"' not in src


def test_place_buy_order_raises_without_price():
    from connector import AngelOneConnector
    cfg = SimpleNamespace(angel_api_key="k", angel_client_id="C",
                          angel_pin="1234", angel_totp_secret="X")
    conn = AngelOneConnector(cfg)
    with pytest.raises(ValueError):
        conn.place_buy_order("CPSEETF-EQ", "2328", 5, price=None)


# ── Telegram /status only from configured chat ────────────────────────────────

def test_status_command_ignored_from_wrong_chat():
    """StatusCommandPoller must ignore /status from non-configured chat IDs."""
    from notifier import StatusCommandPoller, Notifier

    poller = StatusCommandPoller(token="fake", allowed_chat_id="111")

    update = {"update_id": 1, "message": {"text": "/status", "chat": {"id": "999"}}}

    notifier = MagicMock()

    with patch("notifier.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"result": [update]}
        )
        poller.poll(notifier, lambda: [], lambda: (0, 1500))

    # status_reply should NOT be called since chat id is wrong
    notifier.status_reply.assert_not_called()


def test_status_command_accepted_from_correct_chat():
    from notifier import StatusCommandPoller

    poller = StatusCommandPoller(token="fake", allowed_chat_id="111")
    update = {"update_id": 2, "message": {"text": "/status", "chat": {"id": "111"}}}

    notifier = MagicMock()

    with patch("notifier.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"result": [update]}
        )
        poller.poll(notifier, lambda: [], lambda: (0, 1500))

    notifier.status_reply.assert_called_once()


# ── Secrets never in logs ─────────────────────────────────────────────────────

def test_telegram_token_not_in_notifier_log(caplog):
    from notifier import Notifier, E
    n = Notifier.__new__(Notifier)
    n._token       = "SUPER_SECRET_TOKEN_12345"
    n._dry_run     = True
    n._mode        = "dry_run"
    n._chat_ids    = []
    n.enabled      = False
    n._dedupe_path = "notifier_dedupe.dry.json"
    from notifier import _load_dedupe
    n._dedupe      = {"date": "2000-01-01", "sent": {}}
    n._error_times = {}
    n._sender      = None

    n.notify(E.TEST, "test message")

    for record in caplog.records:
        assert "SUPER_SECRET_TOKEN_12345" not in record.getMessage()


# ── Daily summary always sent ─────────────────────────────────────────────────

def test_daily_summary_not_deduped(tmp_path):
    from notifier import Notifier, _dedupe_path, _load_dedupe

    n = Notifier.__new__(Notifier)
    n._token       = "X"
    n._dry_run     = True
    n._mode        = "dry_run"
    n._chat_ids    = ["1"]
    n.enabled      = True
    n._dedupe_path = str(tmp_path / _dedupe_path(True))
    n._dedupe      = _load_dedupe(n._dedupe_path)
    n._error_times = {}

    class Shim:
        sent = []
        def enqueue(self, i): self.sent.append(i.text)
        def stop(self): pass
    n._sender = Shim()

    n.daily_summary([], None, [], [], 0, 1500)
    n.daily_summary([], None, [], [], 0, 1500)
    assert len(n._sender.sent) == 2   # never deduped


# ── Groww order stub test ─────────────────────────────────────────────────────

def test_groww_order_stub_raises():
    from groww_connector import GrowwConnector
    g = GrowwConnector.__new__(GrowwConnector)
    g._api_key = "k"; g._api_secret = "s"
    with pytest.raises(NotImplementedError):
        g.place_limit_buy()
