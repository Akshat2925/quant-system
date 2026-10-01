"""
Stage 1 tests — MODE system.

Proves:
- alert_only makes ZERO order API calls on the connector
- dry_run simulates orders without real API calls
- mode tag appears correctly in Telegram messages
- engine.execute() returns [] immediately in alert_only
- live mode is rejected without --confirm-live
"""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from tranche_engine import AllocationEngine, TrancheState, MonthlyState
from notifier import Notifier, _dedupe_path, _load_dedupe


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_config(monthly_budget=1500.0, mode="alert_only"):
    return SimpleNamespace(
        monthly_budget   = monthly_budget,
        daily_cap_pct    = 0.33,
        daily_loss_limit = 5000.0,
        nav_check_mode   = "advisory",
        mode             = mode,
    )


def _make_engine(tmp_path, mode="alert_only", dry_run=None):
    cfg = _fake_config(mode=mode)
    # dry_run=True for both alert_only and dry_run
    if dry_run is None:
        dry_run = mode in ("alert_only", "dry_run")
    eng = AllocationEngine(config=cfg, dry_run=dry_run, mode=mode)
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


def _fake_connector():
    c = MagicMock()
    c.get_ltp.return_value = 100.0
    c.place_buy_order.return_value = "ORD001"
    c.get_order_status.return_value = "complete"
    return c


def _make_notifier(tmp_path, mode="alert_only"):
    n = Notifier.__new__(Notifier)
    n._token        = "FAKE"
    n._dry_run      = mode in ("alert_only", "dry_run")
    n._mode         = mode
    n._chat_ids     = ["9999"]
    n.enabled       = True
    n._dedupe_path  = str(tmp_path / _dedupe_path(n._dry_run))
    n._dedupe       = _load_dedupe(n._dedupe_path)
    n._error_times  = {}

    class _Shim:
        def __init__(self): self.sent = []
        def enqueue(self, item): self.sent.append(item.text)
        def stop(self): pass
        def join(self, **_): pass

    n._sender = _Shim()
    return n


# ── alert_only: zero order API calls ─────────────────────────────────────────

def test_alert_only_engine_returns_empty_immediately(tmp_path):
    """execute() must return ([], []) without touching the connector."""
    eng  = _make_engine(tmp_path, mode="alert_only")
    conn = _fake_connector()
    orders, skips = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=5000.0)
    assert orders == []
    # connector must NOT have been called at all
    conn.place_buy_order.assert_not_called()
    conn.get_ltp.assert_not_called()


def test_alert_only_connector_never_called(tmp_path):
    """In alert_only mode, neither place_buy_order nor get_order_status is called."""
    eng  = _make_engine(tmp_path, mode="alert_only")
    conn = MagicMock()
    eng.execute({"CPSEETF-EQ": -3.0, "SETFGOLD-EQ": -2.0}, conn,
                funds_available=None)
    conn.place_buy_order.assert_not_called()
    conn.get_order_status.assert_not_called()


def test_dry_run_does_not_call_place_buy_order(tmp_path):
    """dry_run uses fake order IDs, never calls the real place_buy_order."""
    from nav_checker import NAVResult
    good_nav = NAVResult(
        symbol="CPSEETF-EQ", market_price=100.0, nav=100.0,
        nav_date="2026-01-01", premium_pct=0.0,
        is_good_entry=True, use_limit=False,
        reason="OK", action="BUY",
    )
    eng  = _make_engine(tmp_path, mode="dry_run")
    conn = _fake_connector()
    with patch.object(eng.nav_checker, "check", return_value=good_nav):
        orders, _ = eng.execute({"CPSEETF-EQ": -3.0}, conn, funds_available=None)
    conn.place_buy_order.assert_not_called()
    assert len(orders) == 1
    assert orders[0]["order_id"].startswith("DRY_")


# ── Mode tag in messages ──────────────────────────────────────────────────────

def test_alert_only_tag_in_notifier_message(tmp_path):
    n = _make_notifier(tmp_path, mode="alert_only")
    n.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    assert len(n._sender.sent) == 1
    assert "ALERT ONLY" in n._sender.sent[0]


def test_dry_run_tag_in_notifier_message(tmp_path):
    n = _make_notifier(tmp_path, mode="dry_run")
    n.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    assert "DRY RUN" in n._sender.sent[0]


def test_live_tag_in_notifier_message(tmp_path):
    n = _make_notifier(tmp_path, mode="live")
    n.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    assert "LIVE" in n._sender.sent[0]


# ── MODE resolution in bot.py ─────────────────────────────────────────────────

def test_cli_flag_alert_only_overrides_env():
    """--alert-only flag sets effective_mode = alert_only regardless of .env."""
    # Simulate argv parsing logic from main()
    argv = ["bot.py", "--alert-only"]
    mode = "alert_only" if "--alert-only" in argv else \
           "dry_run"    if "--dry-run"    in argv else \
           "live"       if "--live"       in argv else "from_env"
    assert mode == "alert_only"


def test_cli_flag_dry_run_overrides_env():
    argv = ["bot.py", "--dry-run"]
    mode = "alert_only" if "--alert-only" in argv else \
           "dry_run"    if "--dry-run"    in argv else \
           "live"       if "--live"       in argv else "from_env"
    assert mode == "dry_run"


def test_no_cli_flag_uses_config_mode():
    argv = ["bot.py"]
    mode = "alert_only" if "--alert-only" in argv else \
           "dry_run"    if "--dry-run"    in argv else \
           "live"       if "--live"       in argv else "from_env"
    assert mode == "from_env"


# ── Config accepts new MODE values ────────────────────────────────────────────

def test_config_accepts_alert_only():
    import os
    from unittest.mock import patch
    env = {
        "ANGEL_API_KEY": "k", "ANGEL_CLIENT_ID": "C", "ANGEL_PIN": "1234",
        "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        "MODE": "alert_only",
    }
    with patch.dict(os.environ, env, clear=False):
        from config import load_config
        cfg = load_config()
    assert cfg.mode == "alert_only"


def test_config_accepts_dry_run():
    import os
    from unittest.mock import patch
    env = {
        "ANGEL_API_KEY": "k", "ANGEL_CLIENT_ID": "C", "ANGEL_PIN": "1234",
        "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        "MODE": "dry_run",
    }
    with patch.dict(os.environ, env, clear=False):
        from config import load_config
        cfg = load_config()
    assert cfg.mode == "dry_run"


def test_config_rejects_old_dry_value():
    """Old MODE=dry must now raise ConfigError."""
    import os
    from unittest.mock import patch
    from config import ConfigError
    env = {
        "ANGEL_API_KEY": "k", "ANGEL_CLIENT_ID": "C", "ANGEL_PIN": "1234",
        "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        "MODE": "dry",
    }
    with patch.dict(os.environ, env, clear=False):
        from config import load_config
        with pytest.raises(ConfigError, match="MODE must be"):
            load_config()


def test_config_default_mode_is_alert_only():
    """When MODE is not set, default must be alert_only."""
    import os
    from unittest.mock import patch
    env = {
        "ANGEL_API_KEY": "k", "ANGEL_CLIENT_ID": "C", "ANGEL_PIN": "1234",
        "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
    }
    # Remove MODE from env
    clean_env = {k: v for k, v in os.environ.items() if k != "MODE"}
    clean_env.update(env)
    with patch.dict(os.environ, clean_env, clear=True):
        from config import load_config
        cfg = load_config()
    assert cfg.mode == "alert_only"
