"""
Tests for notifier.py.

All Telegram HTTP calls are mocked â€” no real network, no token.
The background sender thread is replaced with a synchronous shim so
tests don't depend on thread timing.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from notifier import (
    E,
    Notifier,
    Templates,
    _dedupe_path,
    _load_dedupe,
    _save_dedupe,
    _mask,
    _SenderThread,
    _QueueItem,
)

# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

FAKE_TOKEN   = "1234567890:ABCDEFGHIJKLMNabcdefghijklmn"
FAKE_CHAT_ID = "9999999"


def _ok_response():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"ok": True}
    return r


def _rate_limit_response(retry_after: int = 2):
    r = MagicMock()
    r.status_code = 429
    r.json.return_value = {"parameters": {"retry_after": retry_after}}
    return r


def _error_response(code: int = 400, body: str = "bad request"):
    r = MagicMock()
    r.status_code = code
    r.text = body
    r.json.return_value = {}
    return r


def _make_notifier(tmp_path: Path, dry_run: bool = True) -> Notifier:
    """Return a Notifier whose background thread is patched to be synchronous."""
    notifier = Notifier.__new__(Notifier)
    notifier._token         = FAKE_TOKEN
    notifier._dry_run       = dry_run
    notifier._mode          = "dry_run" if dry_run else "live"
    notifier._chat_ids      = [FAKE_CHAT_ID]
    notifier.enabled        = True
    notifier._dedupe_path   = str(tmp_path / _dedupe_path(dry_run))
    notifier._dedupe        = _load_dedupe(notifier._dedupe_path)
    notifier._error_times   = {}

    # Replace background thread with a synchronous shim
    class _SyncShim:
        def __init__(self, token):
            self._token = token
            self.sent: list[dict] = []   # captured payloads for assertions
            self._consec_fail = 0

        def enqueue(self, item: _QueueItem):
            self.sent.append({"chat_ids": item.chat_ids, "text": item.text})

        def stop(self):
            pass

        def join(self, **_):
            pass

    notifier._sender = _SyncShim(FAKE_TOKEN)
    return notifier


# â”€â”€ Token masking â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_mask_hides_token():
    masked = _mask(FAKE_TOKEN)
    assert FAKE_TOKEN not in masked
    assert len(masked) < len(FAKE_TOKEN)  # ellipsis appended, token truncated
    assert len(masked) < len(FAKE_TOKEN)


def test_token_never_in_log_output(tmp_path, caplog):
    """Notifier must never write the raw token to the log."""
    notifier = _make_notifier(tmp_path)
    notifier.notify(E.TEST, "Test message")
    for record in caplog.records:
        assert FAKE_TOKEN not in record.getMessage()


def test_token_never_in_exception_log(tmp_path, caplog):
    """Even when enqueue raises with token in the message, it must be masked in logs."""
    notifier = _make_notifier(tmp_path)
    # Make enqueue raise with the token in the exception message
    notifier._sender.enqueue = MagicMock(
        side_effect=Exception(f"Connection to {FAKE_TOKEN} failed")
    )
    # Must not raise
    notifier.notify(E.TEST, "check token leak")
    # Token must not appear in any log record
    for record in caplog.records:
        assert FAKE_TOKEN not in record.getMessage()


# â”€â”€ Dedupe â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_trigger_alert_sent_once_per_day(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    notifier.trigger_hit("CPSEETF-EQ", -2.6, -2.0, 94.0, 91.50)   # same day, same symbol

    # Should only have been enqueued once
    sent = notifier._sender.sent
    assert sum(1 for s in sent if "CPSEETF-EQ" in s["text"]) == 1


def test_trigger_alert_not_resent_after_restart(tmp_path):
    # First notifier sends the alert
    n1 = _make_notifier(tmp_path)
    n1.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    assert len(n1._sender.sent) == 1

    # Second notifier (simulates restart) loads same dedupe file
    n2 = _make_notifier(tmp_path)
    n2.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    assert len(n2._sender.sent) == 0   # dedupe already has this key


def test_escalation_fires_at_deeper_level(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    # Escalation: reset_dedupe is called inside trigger_hit(escalation=True)
    notifier.trigger_hit("CPSEETF-EQ", -3.6, -2.0, 94.0, 90.24, escalation=True)

    sent_texts = [s["text"] for s in notifier._sender.sent]
    assert len(sent_texts) == 2
    assert any("ESCALATION" in t for t in sent_texts)


def test_dedupe_resets_on_new_day(tmp_path):
    state = {"date": "2000-01-01", "sent": {f"{E.TRIGGER_HIT}::CPSEETF": "09:00"}}
    path  = str(tmp_path / _dedupe_path(True))
    _save_dedupe(path, state)

    # Load dedupe â€” old date â†’ fresh state
    loaded = _load_dedupe(path)
    assert loaded["date"] == str(date.today())
    assert loaded["sent"] == {}


def test_daily_summary_never_deduped(tmp_path):
    """DAILY_SUMMARY must always be sent even if called twice."""
    notifier = _make_notifier(tmp_path)
    notifier.daily_summary([], None, [], [], 0, 1500)
    notifier.daily_summary([], None, [], [], 0, 1500)
    assert len(notifier._sender.sent) == 2


# â”€â”€ Skip reasons â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_buy_skipped_contains_reason(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier.buy_skipped("SETFGOLD-EQ", "NAV WAIT â€” stale NAV 5 days old")
    sent = notifier._sender.sent
    assert len(sent) == 1
    assert "NAV WAIT" in sent[0]["text"]
    assert "SETFGOLD-EQ" in sent[0]["text"]


def test_buy_skipped_deduped_by_symbol_and_reason(tmp_path):
    notifier = _make_notifier(tmp_path)
    reason = "NAV unavailable â€” fail-safe WAIT"
    notifier.buy_skipped("SETFGOLD-EQ", reason)
    notifier.buy_skipped("SETFGOLD-EQ", reason)   # same reason
    assert len(notifier._sender.sent) == 1


# â”€â”€ Telegram failures never raise â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_telegram_failure_does_not_raise(tmp_path):
    """Delivery failure must never propagate into the trading loop."""
    notifier = _make_notifier(tmp_path)
    notifier._sender.enqueue = MagicMock(side_effect=RuntimeError("network down"))

    # Must not raise
    try:
        notifier.notify(E.TEST, "should not crash")
    except Exception as exc:
        pytest.fail(f"notify() raised unexpectedly: {exc}")


def test_sender_thread_retries_on_failure():
    """_SenderThread retries up to MAX_RETRIES on non-200 response."""
    thread = _SenderThread(FAKE_TOKEN)
    fail_resp = _error_response(500, "server error")

    with patch("notifier.requests.post", return_value=fail_resp) as mock_post:
        result = thread._send_one(FAKE_CHAT_ID, "test")
    assert not result
    assert mock_post.call_count == 3   # _MAX_RETRIES


def test_sender_thread_honours_429_retry_after():
    """On 429, thread should sleep for retry_after seconds (mocked)."""
    thread   = _SenderThread(FAKE_TOKEN)
    ok_resp  = _ok_response()
    rl_resp  = _rate_limit_response(retry_after=1)

    responses = [rl_resp, ok_resp]
    with patch("notifier.requests.post", side_effect=responses):
        with patch("notifier.time.sleep") as mock_sleep:
            result = thread._send_one(FAKE_CHAT_ID, "test")

    assert result
    # Should have slept for retry_after value
    mock_sleep.assert_called_with(1)


def test_sender_thread_masks_token_in_exception(caplog):
    thread = _SenderThread(FAKE_TOKEN)
    with patch(
        "notifier.requests.post",
        side_effect=Exception(f"failed connecting to {FAKE_TOKEN}"),
    ):
        thread._send_one(FAKE_CHAT_ID, "test")

    for record in caplog.records:
        assert FAKE_TOKEN not in record.getMessage()


# â”€â”€ Message content â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_html_escape_in_templates():
    """Dynamic text with HTML special chars must be escaped."""
    text = Templates.buy_skipped(True, "<script>", "reason & more <b>bold</b>")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_message_truncated_at_4000_chars():
    long_body = "x" * 5000
    text = Templates.error(True, "TEST", long_body)
    assert len(text) <= 4000


def test_mode_tag_present_in_all_templates():
    """Every template must include the [DRY RUN] or [LIVE] mode tag."""
    checks = [
        Templates.bot_started(True, "09:10", [], 0, 1500, 495),
        Templates.heartbeat(True, 5, "09:20"),
        Templates.trigger_hit(True, "CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65),
        Templates.buy_window_open(True, []),
        Templates.buy_skipped(True, "CPSEETF-EQ", "NAV WAIT"),
        Templates.order_filled(True, "CPSEETF-EQ", 5, 91.65, 458.25, "X1", 0.0, 500, 1000),
        Templates.daily_summary(True, [], None, [], [], 0, 1500),
        Templates.bot_stopped(True, "manual"),
    ]
    for msg in checks:
        assert "DRY RUN" in msg or "LIVE" in msg, f"Mode tag missing:\n{msg[:200]}"


def test_order_filled_contains_key_fields():
    text = Templates.order_filled(
        False, "CPSEETF-EQ", 5, 91.65, 458.25, "ORD001", -0.23, 758.25, 741.75
    )
    assert "CPSEETF-EQ" in text
    assert "ORD001" in text
    assert "458" in text
    # budget remaining rounds to 742 â€” check the integer part is present
    assert "74" in text


def test_daily_summary_no_trigger_message(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier.daily_summary(
        etf_changes    = [{"symbol": "CPSEETF-EQ", "change_pct": -0.5, "triggered": False}],
        nifty_pct      = None,
        triggered_syms = [],
        orders         = [],
        budget_used    = 0,
        budget_total   = 1500,
    )
    text = notifier._sender.sent[0]["text"]
    assert "No trigger" in text


# â”€â”€ Error rate-limiting â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_error_rate_limited(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier._error_times = {}  # guaranteed fresh — isolated from other tests
    notifier._sender.sent.clear()
    notifier.notify_error(E.LOGIN_FAILED, "bad credentials")
    first_count = len(notifier._sender.sent)
    notifier.notify_error(E.LOGIN_FAILED, "bad credentials")   # within 30 min
    second_count = len(notifier._sender.sent)
    assert first_count == 1, f"first call should send 1, got {first_count}"
    assert second_count == 1, f"second call should be rate-limited, got {second_count}"


def test_different_error_types_not_rate_limited_together(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier._error_times = {}  # guaranteed fresh — isolated from other tests
    notifier._sender.sent.clear()
    notifier.notify_error(E.LOGIN_FAILED,       "error 1")
    notifier.notify_error(E.PRICE_FEED_FAILING, "error 2")
    assert len(notifier._sender.sent) == 2,         f"two different error types should both send, got {len(notifier._sender.sent)}"


# â”€â”€ Dry-run separation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_dry_and_live_use_separate_dedupe_files(tmp_path):
    dry_path  = str(tmp_path / _dedupe_path(True))
    live_path = str(tmp_path / _dedupe_path(False))
    assert dry_path != live_path


def test_dry_run_tag_in_message(tmp_path):
    notifier = _make_notifier(tmp_path, dry_run=True)
    notifier.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    text = notifier._sender.sent[0]["text"]
    assert "DRY RUN" in text


def test_live_tag_in_message(tmp_path):
    notifier = _make_notifier(tmp_path, dry_run=False)
    notifier.trigger_hit("CPSEETF-EQ", -2.5, -2.0, 94.0, 91.65)
    text = notifier._sender.sent[0]["text"]
    assert "LIVE" in text


# â”€â”€ Dual chat ID â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_dual_chat_id_sends_to_both(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier._chat_ids = [FAKE_CHAT_ID, "8888888"]
    notifier.notify(E.TEST, "dual send test")
    sent = notifier._sender.sent
    assert len(sent) == 1
    assert set(sent[0]["chat_ids"]) == {FAKE_CHAT_ID, "8888888"}
