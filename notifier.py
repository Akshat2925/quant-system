"""
Notifier — professional, reliable, non-blocking Telegram notification layer.

Design goals
────────────
• Single notify() entry point; all formatting lives here.
• Background thread + queue: sending NEVER blocks the trading loop.
• Dedupe: each (date, event_type, dedupe_key) fires at most once per day.
  State persisted to JSON so restarts do not re-send.
• Retry with exponential backoff; honours Telegram 429 retry_after.
• Dual chat-ID support (TELEGRAM_CHAT_ID + TELEGRAM_CHAT_ID_2).
• Token never appears in logs, exceptions, or error messages.
• HTML parse_mode; all dynamic text html.escaped; messages capped at 4000 chars.
• Rate-limit identical ERROR events to once per 30 minutes.
• Dry-run messages clearly tagged; separate dedupe file.
"""

from __future__ import annotations

import html
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from loguru import logger

IST = ZoneInfo("Asia/Kolkata")

# ── Constants ─────────────────────────────────────────────────────────────────
_SEND_URL        = "https://api.telegram.org/bot{token}/sendMessage"
_UPDATES_URL     = "https://api.telegram.org/bot{token}/getUpdates"
_MAX_MSG_LEN     = 4000
_SEND_TIMEOUT    = 10        # seconds per HTTP call
_MAX_RETRIES     = 3
_BASE_DELAY      = 2.0       # first retry delay (doubles each time)
_ERROR_RATELIMIT = 1800      # 30 min between identical error alerts
_LOUD_FAIL_AFTER = 5         # consecutive failures before loud warning

# Event type constants
class E:
    BOT_STARTED           = "BOT_STARTED"
    HEARTBEAT             = "HEARTBEAT"
    TRIGGER_HIT           = "TRIGGER_HIT"
    TRIGGER_ESCALATION    = "TRIGGER_ESCALATION"
    TRIGGER_RECOVERED     = "TRIGGER_RECOVERED"
    BUY_WINDOW_OPEN       = "BUY_WINDOW_OPEN"
    BUY_SKIPPED           = "BUY_SKIPPED"
    WINDOW_CLOSED_NOTHING = "WINDOW_CLOSED_NOTHING"
    ORDER_PLACED          = "ORDER_PLACED"
    ORDER_FILLED          = "ORDER_FILLED"
    ORDER_REJECTED        = "ORDER_REJECTED"
    ORDER_UNFILLED        = "ORDER_UNFILLED"
    BUDGET_LOW            = "BUDGET_LOW"
    BUDGET_EXHAUSTED      = "BUDGET_EXHAUSTED"
    DAILY_SUMMARY         = "DAILY_SUMMARY"
    NIFTY_FALL            = "NIFTY_FALL"
    LOGIN_FAILED          = "LOGIN_FAILED"
    RELOGIN_OK            = "RELOGIN_OK"
    PRICE_FEED_FAILING    = "PRICE_FEED_FAILING"
    STATE_CORRUPT         = "STATE_CORRUPT"
    BOT_CRASHED           = "BOT_CRASHED"
    BOT_STOPPED           = "BOT_STOPPED"
    STATUS_REPLY          = "STATUS_REPLY"
    TEST                  = "TEST"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mask(token: str) -> str:
    if not token:
        return "(not set)"
    return token[:8] + "…"


def _h(text: Any) -> str:
    """HTML-escape and stringify."""
    return html.escape(str(text))


def _cap(msg: str) -> str:
    if len(msg) > _MAX_MSG_LEN:
        return msg[:_MAX_MSG_LEN - 20] + "\n…<i>(truncated)</i>"
    return msg


def _mode_tag(dry_run: bool = True, mode: str = "") -> str:
    """Return HTML mode tag. If mode string is provided, use it directly."""
    if mode:
        tags = {"alert_only": "📊 <b>[ALERT ONLY]</b>",
                "dry_run":    "🧪 <b>[DRY RUN]</b>",
                "live":       "💰 <b>[LIVE]</b>"}
        return tags.get(mode, "📊 <b>[ALERT ONLY]</b>")
    return "🧪 <b>[DRY RUN]</b>" if dry_run else "💰 <b>[LIVE]</b>"


def _now_ist_str() -> str:
    return datetime.now(IST).strftime("%H:%M:%S IST")


# ── Dedupe state ──────────────────────────────────────────────────────────────

def _dedupe_path(dry_run: bool) -> str:
    return "notifier_dedupe.dry.json" if dry_run else "notifier_dedupe.json"


def _load_dedupe(path: str) -> dict:
    today = str(date.today())
    try:
        with open(path) as f:
            d = json.load(f)
        if d.get("date") == today:
            return d
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"date": today, "sent": {}}


def _save_dedupe(path: str, state: dict) -> None:
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".tmp_notif_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        logger.warning(f"⚠️ Could not save dedupe state: {e}")


# ── Message templates ─────────────────────────────────────────────────────────

class Templates:
    """All message formatting lives here — nowhere else."""

    @staticmethod
    def bot_started(dry_run: bool, time_str: str, watchlist: list[dict],
                    budget_used: float, budget_total: float,
                    daily_cap: float, mode: str = "") -> str:
        remaining = max(0.0, budget_total - budget_used)
        lines = [
            f"🚀 <b>BOT STARTED</b>  {_mode_tag(dry_run, mode=mode)}",
            f"⏰ {_h(time_str)}",
            "",
            "<b>Watchlist</b>",
        ]
        for w in watchlist:
            sym    = _h(w.get("symbol", "?"))
            ltp    = w.get("ltp")
            open_  = w.get("day_open")
            pct    = w.get("change_pct")
            trig   = w.get("trigger_pct")
            ltp_s  = f"₹{ltp:.2f}" if ltp else "N/A"
            open_s = f"₹{open_:.2f}" if open_ else "N/A"
            pct_s  = f"{pct:+.2f}%" if pct is not None else "N/A"
            trig_s = f"{trig:+.1f}%" if trig is not None else "?"
            lines.append(
                f"  • <b>{sym}</b>  {ltp_s}  ({pct_s})  open {open_s}  trigger {trig_s}"
            )
        lines += [
            "",
            f"📅 Monthly: ₹{budget_used:.0f} used / ₹{remaining:.0f} left",
            f"📊 Today's cap: ₹{daily_cap:.0f}",
        ]
        return _cap("\n".join(lines))

    @staticmethod
    def heartbeat(dry_run: bool, n_etfs: int, time_str: str, mode: str = "") -> str:
        return _cap(
            f"💓 <b>HEARTBEAT</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"⏰ {_h(time_str)}  —  watching {n_etfs} ETFs"
        )

    @staticmethod
    def trigger_hit(dry_run: bool, symbol: str, change_pct: float,
                    trigger_pct: float, day_open: float, ltp: float,
                    escalation: bool = False, mode: str = "") -> str:
        tag   = "📈 <b>TRIGGER ESCALATION</b>" if escalation else "🎯 <b>TRIGGER HIT</b>"
        return _cap(
            f"{tag}  {_mode_tag(dry_run, mode=mode)}\n"
            f"<b>{_h(symbol)}</b>\n"
            f"Change:  <b>{change_pct:+.2f}%</b>  (trigger {trigger_pct:+.1f}%)\n"
            f"Price:   ₹{ltp:.2f}  |  Open: ₹{day_open:.2f}\n"
            f"⏰ Buy window: 3:00–3:15 PM IST"
        )

    @staticmethod
    def trigger_recovered(dry_run: bool, symbol: str,
                          change_pct: float, ltp: float, mode: str = "") -> str:
        return _cap(
            f"↩️ <b>TRIGGER RECOVERED</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"<b>{_h(symbol)}</b> back above trigger\n"
            f"Change: {change_pct:+.2f}%  |  Price: ₹{ltp:.2f}"
        )

    @staticmethod
    def buy_window_open(dry_run: bool,
                        plan: list[dict], mode: str = "") -> str:
        lines = [f"🕒 <b>BUY WINDOW OPEN</b>  {_mode_tag(dry_run, mode=mode)}", ""]
        if not plan:
            lines.append("No triggered ETFs — nothing planned.")
        else:
            lines.append("<b>Planned allocations (pre-NAV check)</b>")
            for p in plan:
                sym   = _h(p.get("symbol", "?"))
                pct   = p.get("change_pct", 0)
                alloc = p.get("allocation", 0)
                lines.append(
                    f"  • <b>{sym}</b>  {pct:+.2f}%  →  ₹{alloc:.0f}"
                )
        return _cap("\n".join(lines))

    @staticmethod
    def buy_skipped(dry_run: bool, symbol: str, reason: str, mode: str = "") -> str:
        return _cap(
            f"⏭️ <b>BUY SKIPPED</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"<b>{_h(symbol)}</b>\n"
            f"Reason: {_h(reason)}"
        )

    @staticmethod
    def window_closed_nothing(dry_run: bool,
                              triggered: list[str], mode: str = "") -> str:
        syms = ", ".join(_h(s) for s in triggered)
        return _cap(
            f"🚪 <b>WINDOW CLOSED — NOTHING BOUGHT</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"Triggered: {syms}\n"
            f"All buys were skipped (see individual BUY_SKIPPED messages)."
        )

    @staticmethod
    def order_placed(dry_run: bool, symbol: str, qty: int,
                     price: float, amount: float, order_id: str,
                     order_type: str, mode: str = "") -> str:
        return _cap(
            f"📤 <b>ORDER PLACED</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"<b>{_h(symbol)}</b>  ×{qty}  @ ₹{price:.2f}\n"
            f"Amount: ₹{amount:.2f}  |  Type: {_h(order_type)}\n"
            f"Order ID: <code>{_h(order_id)}</code>"
        )

    @staticmethod
    def order_filled(dry_run: bool, symbol: str, qty: int,
                     avg_price: float, amount: float, order_id: str,
                     nav_premium: float, budget_used: float,
                     budget_remaining: float, mode: str = "") -> str:
        return _cap(
            f"✅ <b>ORDER FILLED</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"<b>{_h(symbol)}</b>  ×{qty}  @ ₹{avg_price:.2f}\n"
            f"Amount: ₹{amount:.2f}  |  NAV premium: {nav_premium:+.2f}%\n"
            f"Order ID: <code>{_h(order_id)}</code>\n"
            f"📅 Month: ₹{budget_used:.0f} used / ₹{budget_remaining:.0f} left"
        )

    @staticmethod
    def order_rejected(dry_run: bool, symbol: str,
                       order_id: str, reason: str, mode: str = "") -> str:
        return _cap(
            f"❌ <b>ORDER REJECTED</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"<b>{_h(symbol)}</b>\n"
            f"Order ID: <code>{_h(order_id)}</code>\n"
            f"Reason: {_h(reason)}\n"
            f"Budget NOT charged."
        )

    @staticmethod
    def order_unfilled(dry_run: bool, symbol: str,
                       order_id: str, status: str, mode: str = "") -> str:
        return _cap(
            f"⚠️ <b>ORDER UNFILLED</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"<b>{_h(symbol)}</b>\n"
            f"Order ID: <code>{_h(order_id)}</code>  |  Status: {_h(status)}\n"
            f"Budget NOT charged. Order cancelled if LIMIT."
        )

    @staticmethod
    def budget_low(dry_run: bool, budget_used: float,
                   budget_total: float, pct_used: float, mode: str = "") -> str:
        remaining = max(0.0, budget_total - budget_used)
        return _cap(
            f"⚠️ <b>BUDGET LOW</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"Only {100 - pct_used:.0f}% remaining this month\n"
            f"Used: ₹{budget_used:.0f} / ₹{budget_total:.0f}\n"
            f"Remaining: ₹{remaining:.0f}"
        )

    @staticmethod
    def budget_exhausted(dry_run: bool, budget_total: float, mode: str = "") -> str:
        return _cap(
            f"🚫 <b>BUDGET EXHAUSTED</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"Monthly budget ₹{budget_total:.0f} fully used.\n"
            f"No more buys this month."
        )

    @staticmethod
    def daily_summary(dry_run: bool, etf_changes: list[dict],
                      nifty_pct: float | None,
                      triggered_syms: list[str],
                      orders: list[dict],
                      budget_used: float, budget_total: float, mode: str = "") -> str:
        remaining = max(0.0, budget_total - budget_used)
        lines = [f"📋 <b>DAILY SUMMARY</b>  {_mode_tag(dry_run, mode=mode)}", ""]

        # ETF changes
        lines.append("<b>ETF performance today</b>")
        for e in etf_changes:
            sym = _h(e.get("symbol", "?"))
            pct = e.get("change_pct")
            pct_s = f"{pct:+.2f}%" if pct is not None else "N/A"
            trig  = "🎯" if e.get("triggered") else "  "
            lines.append(f"  {trig} <b>{sym}</b>  {pct_s}")

        if nifty_pct is not None:
            lines.append(f"  📈 Nifty:  {nifty_pct:+.2f}%")

        lines.append("")
        if triggered_syms:
            lines.append(f"Triggers hit: {', '.join(_h(s) for s in triggered_syms)}")
        else:
            lines.append("No trigger hit today.")

        lines.append("")
        if orders:
            total_spent = sum(o.get("amount", 0) for o in orders)
            lines.append(f"<b>Bought today</b>  (total ₹{total_spent:.0f})")
            for o in orders:
                sym   = _h(o.get("symbol", "?"))
                qty   = o.get("quantity", 0)
                price = o.get("price", 0)
                amt   = o.get("amount", 0)
                lines.append(
                    f"  • {sym}  ×{qty} @ ₹{price:.2f}  = ₹{amt:.0f}"
                )
        else:
            lines.append("Nothing bought today.")

        lines += [
            "",
            f"📅 Month: ₹{budget_used:.0f} used / ₹{remaining:.0f} left",
        ]
        return _cap("\n".join(lines))

    @staticmethod
    def nifty_fall(dry_run: bool, change_pct: float, reason: str, mode: str = "") -> str:
        return _cap(
            f"📉 <b>NIFTY FALL</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"Change: {change_pct:+.2f}%\n"
            f"{_h(reason)}"
        )

    @staticmethod
    def error(dry_run: bool, event_type: str, detail: str, mode: str = "") -> str:
        return _cap(
            f"🔴 <b>ERROR: {_h(event_type)}</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"{_h(detail[:300])}"
        )

    @staticmethod
    def bot_stopped(dry_run: bool, reason: str, mode: str = "") -> str:
        return _cap(
            f"⛔ <b>BOT STOPPED</b>  {_mode_tag(dry_run, mode=mode)}\n"
            f"Reason: {_h(reason)}"
        )

    @staticmethod
    def status_reply(dry_run: bool, watchlist: list[dict],
                     budget_used: float, budget_total: float, mode: str = "") -> str:
        remaining = max(0.0, budget_total - budget_used)
        lines = [
            f"📊 <b>STATUS</b>  {_mode_tag(dry_run, mode=mode)}",
            f"⏰ {_now_ist_str()}",
            "",
            "<b>Current prices</b>",
        ]
        for w in watchlist:
            sym  = _h(w.get("symbol", "?"))
            ltp  = w.get("ltp")
            pct  = w.get("change_pct")
            ltp_s = f"₹{ltp:.2f}" if ltp else "N/A"
            pct_s = f"{pct:+.2f}%" if pct is not None else "N/A"
            trig  = "🎯" if w.get("triggered") else "  "
            lines.append(f"  {trig} <b>{sym}</b>  {ltp_s}  ({pct_s})")
        lines += [
            "",
            f"📅 Month: ₹{budget_used:.0f} used / ₹{remaining:.0f} left",
        ]
        return _cap("\n".join(lines))

    @staticmethod
    def test_sample(event_label: str) -> str:
        return _cap(
            f"🧪 <b>[TEST] {_h(event_label)}</b>\n"
            f"This is a test message — no real trading data."
        )


# ── Background sender ─────────────────────────────────────────────────────────

@dataclass
class _QueueItem:
    chat_ids:  list[str]
    text:      str
    dedupe_key: str | None  # None = always send


class _SenderThread(threading.Thread):
    """Background thread that drains the send queue."""

    def __init__(self, token: str):
        super().__init__(daemon=True, name="notifier-sender")
        self._token   = token
        self._q: queue.Queue[_QueueItem | None] = queue.Queue(maxsize=200)
        self._consec_fail = 0

    def enqueue(self, item: _QueueItem) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            logger.warning("⚠️ Telegram send queue full — message dropped (logged above)")

    def stop(self) -> None:
        self._q.put(None)   # sentinel

    def run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            self._deliver(item)

    def _deliver(self, item: _QueueItem) -> None:
        for chat_id in item.chat_ids:
            self._send_one(chat_id, item.text)

    def _send_one(self, chat_id: str, text: str) -> bool:
        url = _SEND_URL.format(token=self._token)
        payload = {
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }
        delay = _BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(url, data=payload, timeout=_SEND_TIMEOUT)
                if resp.status_code == 200:
                    self._consec_fail = 0
                    logger.debug("📱 Telegram message delivered")
                    return True
                if resp.status_code == 429:
                    retry_after = float(
                        resp.json().get("parameters", {}).get("retry_after", delay)
                    )
                    logger.warning(
                        f"⚠️ Telegram 429 rate-limit — waiting {retry_after:.0f}s"
                    )
                    time.sleep(retry_after)
                    continue
                if resp.status_code == 400:
                    # Message too long or bad HTML — log and give up
                    logger.error(
                        f"❌ Telegram 400 bad request: {resp.text[:120]} "
                        f"(token masked)"
                    )
                    return False
                logger.warning(
                    f"⚠️ Telegram HTTP {resp.status_code} "
                    f"(attempt {attempt}/{_MAX_RETRIES}): {resp.text[:80]}"
                )
            except Exception as exc:
                # Mask any accidental token leakage in exception messages
                safe_exc = re.sub(re.escape(self._token), _mask(self._token), str(exc))
                logger.warning(
                    f"⚠️ Telegram send error (attempt {attempt}/{_MAX_RETRIES}): "
                    f"{type(exc).__name__}: {safe_exc}"
                )
            if attempt < _MAX_RETRIES:
                time.sleep(delay)
                delay *= 2

        # All retries exhausted
        self._consec_fail += 1
        level = "warning"
        if self._consec_fail >= _LOUD_FAIL_AFTER:
            level = "error"
        getattr(logger, level)(
            f"{'❌❌' if level == 'error' else '⚠️'} Telegram delivery failed after "
            f"{_MAX_RETRIES} retries (consecutive failures: {self._consec_fail}). "
            f"Message is in the log file."
        )
        return False


# ── Public Notifier class ─────────────────────────────────────────────────────

class Notifier:
    """
    Single entry point for all bot notifications.

    Usage:
        notifier = Notifier(token, chat_id, dry_run=False, chat_id_2="")
        notifier.notify(E.TRIGGER_HIT, dedupe_key="CPSEETF",
                        text=Templates.trigger_hit(...))
    """

    def __init__(self, token: str, chat_id: str,
                 dry_run: bool = False, chat_id_2: str = "",
                 mode: str = "alert_only"):
        self._token     = token
        self._dry_run   = dry_run
        self._mode      = mode
        self._chat_ids  = [c for c in [chat_id, chat_id_2] if c]
        self.enabled    = bool(token and chat_id)
        self._dedupe_path = _dedupe_path(dry_run)
        self._dedupe      = _load_dedupe(self._dedupe_path)
        self._error_times: dict[str, float] = {}  # event → last sent epoch

        if self.enabled:
            self._sender = _SenderThread(token)
            self._sender.start()
            logger.info(
                f"📱 Notifier ready (token: {_mask(token)}, "
                f"chat_ids: {len(self._chat_ids)}, "
                f"mode: {mode})"
            )
        else:
            self._sender = None
            logger.info("📱 Telegram not configured — notifications only in logs")

    def _mode_tag(self) -> str:
        """HTML mode tag shown in every message."""
        tags = {
            "alert_only": "📊 <b>[ALERT ONLY]</b>",
            "dry_run":    "🧪 <b>[DRY RUN]</b>",
            "live":       "💰 <b>[LIVE]</b>",
        }
        return tags.get(self._mode, _mode_tag(self._dry_run))

    # ── Core ──────────────────────────────────────────────────────────────

    def notify(self, event_type: str, text: str,
               dedupe_key: str | None = None,
               level: str = "info") -> None:
        """
        event_type  : one of E.*
        text        : already-formatted HTML string (use Templates.*)
        dedupe_key  : if set, this (event_type, dedupe_key) fires at most once
                      per trading day.  None = always send.
        level       : "info" | "warning" | "error" (for log level)
        """
        # Always log
        log_fn = getattr(logger, level, logger.info)
        # Strip HTML tags for clean log output
        clean = re.sub(r"<[^>]+>", "", text).strip()
        log_fn(f"🔔 [{event_type}] {clean[:200]}")

        if not self.enabled or not self._chat_ids:
            return

        # Dedupe check
        if dedupe_key is not None:
            key = f"{event_type}::{dedupe_key}"
            if key in self._dedupe.get("sent", {}):
                logger.debug(f"🔕 Dedupe: {key} already sent today — skipping")
                return
            self._dedupe.setdefault("sent", {})[key] = _now_ist_str()
            _save_dedupe(self._dedupe_path, self._dedupe)

        item = _QueueItem(
            chat_ids   = self._chat_ids,
            text       = text,
            dedupe_key = dedupe_key,
        )
        if self._sender:
            try:
                self._sender.enqueue(item)
            except Exception as exc:
                safe = re.sub(re.escape(self._token), _mask(self._token), str(exc))
                logger.error(f"❌ Failed to enqueue notification: {type(exc).__name__}: {safe}")

    def notify_error(self, event_type: str, detail: str) -> None:
        """Rate-limited error notification (max once per 30 min per event_type)."""
        now = time.monotonic()
        last = self._error_times.get(event_type, None)
        if last is not None and now - last < _ERROR_RATELIMIT:
            logger.debug(f"🔕 Error rate-limit: {event_type} — not resending")
            return
        self._error_times[event_type] = now
        text = Templates.error(self._dry_run, event_type, detail, mode=self._mode)
        self.notify(event_type, text, dedupe_key=None, level="error")

    def reset_dedupe_for_key(self, event_type: str, dedupe_key: str) -> None:
        """Allow a previously-deduped event to fire again (e.g. escalation)."""
        key = f"{event_type}::{dedupe_key}"
        self._dedupe.get("sent", {}).pop(key, None)
        _save_dedupe(self._dedupe_path, self._dedupe)

    def stop(self) -> None:
        if self._sender:
            self._sender.stop()
            self._sender.join(timeout=15)

    # ── Convenience wrappers (keep bot.py clean) ──────────────────────────

    def bot_started(self, watchlist: list, budget_used: float,
                    budget_total: float, daily_cap: float) -> None:
        text = Templates.bot_started(
            self._dry_run, _now_ist_str(), watchlist,
            budget_used, budget_total, daily_cap, mode=self._mode
        )
        self.notify(E.BOT_STARTED, text, dedupe_key="startup")

    def heartbeat(self, n_etfs: int) -> None:
        text = Templates.heartbeat(self._dry_run, n_etfs, _now_ist_str(),
                                   mode=self._mode)
        self.notify(E.HEARTBEAT, text, dedupe_key="heartbeat")

    def trigger_hit(self, symbol: str, change_pct: float,
                    trigger_pct: float, day_open: float, ltp: float,
                    escalation: bool = False) -> None:
        event = E.TRIGGER_ESCALATION if escalation else E.TRIGGER_HIT
        if escalation:
            self.reset_dedupe_for_key(event, symbol)
        text = Templates.trigger_hit(
            self._dry_run, symbol, change_pct,
            trigger_pct, day_open, ltp, escalation, mode=self._mode
        )
        self.notify(event, text, dedupe_key=symbol)

    def trigger_recovered(self, symbol: str, change_pct: float,
                          ltp: float) -> None:
        text = Templates.trigger_recovered(
            self._dry_run, symbol, change_pct, ltp, mode=self._mode
        )
        self.notify(E.TRIGGER_RECOVERED, text, dedupe_key=symbol)

    def buy_window_open(self, plan: list) -> None:
        text = Templates.buy_window_open(self._dry_run, plan, mode=self._mode)
        self.notify(E.BUY_WINDOW_OPEN, text, dedupe_key="window")

    def buy_skipped(self, symbol: str, reason: str) -> None:
        text = Templates.buy_skipped(self._dry_run, symbol, reason,
                                     mode=self._mode)
        self.notify(E.BUY_SKIPPED, text,
                    dedupe_key=f"{symbol}::{reason[:40]}")

    def window_closed_nothing(self, triggered: list) -> None:
        text = Templates.window_closed_nothing(
            self._dry_run, triggered, mode=self._mode
        )
        self.notify(E.WINDOW_CLOSED_NOTHING, text, dedupe_key="nothing_bought")

    def order_placed(self, symbol: str, qty: int, price: float,
                     amount: float, order_id: str, order_type: str) -> None:
        text = Templates.order_placed(
            self._dry_run, symbol, qty, price, amount,
            order_id, order_type, mode=self._mode
        )
        self.notify(E.ORDER_PLACED, text)

    def order_filled(self, symbol: str, qty: int, avg_price: float,
                     amount: float, order_id: str, nav_premium: float,
                     budget_used: float, budget_remaining: float) -> None:
        text = Templates.order_filled(
            self._dry_run, symbol, qty, avg_price, amount,
            order_id, nav_premium, budget_used, budget_remaining,
            mode=self._mode
        )
        self.notify(E.ORDER_FILLED, text)

    def order_rejected(self, symbol: str, order_id: str,
                       reason: str = "") -> None:
        text = Templates.order_rejected(
            self._dry_run, symbol, order_id, reason, mode=self._mode
        )
        self.notify(E.ORDER_REJECTED, text, level="warning")

    def order_unfilled(self, symbol: str, order_id: str,
                       status: str = "timeout") -> None:
        text = Templates.order_unfilled(
            self._dry_run, symbol, order_id, status, mode=self._mode
        )
        self.notify(E.ORDER_UNFILLED, text, level="warning")

    def budget_low(self, budget_used: float, budget_total: float) -> None:
        pct_used = (budget_used / budget_total * 100) if budget_total else 0
        text = Templates.budget_low(
            self._dry_run, budget_used, budget_total, pct_used,
            mode=self._mode
        )
        self.notify(E.BUDGET_LOW, text, dedupe_key="budget_low")

    def budget_exhausted(self, budget_total: float) -> None:
        text = Templates.budget_exhausted(self._dry_run, budget_total,
                                          mode=self._mode)
        self.notify(E.BUDGET_EXHAUSTED, text, dedupe_key="exhausted")

    def daily_summary(self, etf_changes: list, nifty_pct,
                      triggered_syms: list, orders: list,
                      budget_used: float, budget_total: float) -> None:
        text = Templates.daily_summary(
            self._dry_run, etf_changes, nifty_pct,
            triggered_syms, orders, budget_used, budget_total,
            mode=self._mode
        )
        self.notify(E.DAILY_SUMMARY, text)

    def nifty_fall(self, change_pct: float, reason: str) -> None:
        text = Templates.nifty_fall(self._dry_run, change_pct, reason,
                                    mode=self._mode)
        self.notify(E.NIFTY_FALL, text, dedupe_key="nifty_fall")

    def bot_stopped(self, reason: str) -> None:
        text = Templates.bot_stopped(self._dry_run, reason, mode=self._mode)
        self.notify(E.BOT_STOPPED, text)
        self.stop()

    def status_reply(self, chat_id: str, watchlist: list,
                     budget_used: float, budget_total: float) -> None:
        text = Templates.status_reply(
            self._dry_run, watchlist, budget_used, budget_total,
            mode=self._mode
        )
        if self._sender:
            self._sender.enqueue(
                _QueueItem(chat_ids=[chat_id], text=text, dedupe_key=None)
            )



# ── /status command poller ────────────────────────────────────────────────────

class StatusCommandPoller:
    """
    Polls Telegram getUpdates for /status commands.
    Only responds to the configured chat_id (ignores all other senders).
    Never places orders or changes settings.
    """

    def __init__(self, token: str, allowed_chat_id: str):
        self._token          = token
        self._allowed        = allowed_chat_id
        self._offset: int    = 0

    _AUTO_REPLY = (
        "🤖 <b>This is a one-way alert bot.</b>\n\n"
        "I only send trading notifications — I don't read messages.\n\n"
        "Supported command:\n"
        "  /status — get current watchlist &amp; budget\n\n"
        "To stop alerts, mute this chat in Telegram settings."
    )

    def poll(self, notifier: "Notifier",
             watchlist_fn, budget_fn) -> None:
        """Call this once per cycle. watchlist_fn() and budget_fn() are
        zero-arg callables that return current data without any side effects."""
        if not self._token:
            return
        url = _UPDATES_URL.format(token=self._token)
        try:
            resp = requests.get(
                url,
                params={"offset": self._offset, "timeout": 1},
                timeout=5,
            )
            if resp.status_code != 200:
                return
            updates = resp.json().get("result", [])
            for upd in updates:
                self._offset = upd["update_id"] + 1
                msg  = upd.get("message", {})
                text = msg.get("text", "").strip()
                cid  = str(msg.get("chat", {}).get("id", ""))

                if not cid:
                    continue

                if text == "/status" and cid == self._allowed:
                    wl     = watchlist_fn()
                    bu, bt = budget_fn()
                    notifier.status_reply(cid, wl, bu, bt)
                else:
                    # Every message that is not /status gets auto-reply
                    self._send_auto_reply(cid)

        except Exception as e:
            safe = re.sub(re.escape(self._token), _mask(self._token), str(e))
            logger.debug(f"Status poller: {safe}")

    def _send_auto_reply(self, chat_id: str) -> None:
        url = _SEND_URL.format(token=self._token)
        try:
            requests.post(
                url,
                data={"chat_id": chat_id, "text": self._AUTO_REPLY,
                      "parse_mode": "HTML"},
                timeout=_SEND_TIMEOUT,
            )
        except Exception as e:
            safe = re.sub(re.escape(self._token), _mask(self._token), str(e))
            logger.debug(f"Auto-reply error: {safe}")


# ── Test-telegram helper ──────────────────────────────────────────────────────

def send_test_messages(notifier: "Notifier") -> None:
    """Send one sample of every message type tagged [TEST]. Used by --test-telegram."""
    from nav_checker import ETF_LIST

    dry = notifier._dry_run
    sample_watchlist = [
        {"symbol": s, "ltp": 100.0, "day_open": 102.0,
         "change_pct": -1.96, "trigger_pct": info["trigger_pct"],
         "triggered": False}
        for s, info in list(ETF_LIST.items())[:3]
    ]

    msgs = [
        (E.BOT_STARTED,        Templates.bot_started(dry, "09:10:00 IST", sample_watchlist, 300, 1500, 495)),
        (E.HEARTBEAT,          Templates.heartbeat(dry, 5, "09:20:00 IST")),
        (E.TRIGGER_HIT,        Templates.trigger_hit(dry, "CPSEETF", -2.5, -2.0, 94.0, 91.65)),
        (E.TRIGGER_ESCALATION, Templates.trigger_hit(dry, "CPSEETF", -3.6, -2.0, 94.0, 90.24, escalation=True)),
        (E.TRIGGER_RECOVERED,  Templates.trigger_recovered(dry, "CPSEETF", -1.2, 92.87)),
        (E.BUY_WINDOW_OPEN,    Templates.buy_window_open(dry, [{"symbol": "CPSEETF", "change_pct": -2.5, "allocation": 495}])),
        (E.BUY_SKIPPED,        Templates.buy_skipped(dry, "SETFGOLD", "NAV unavailable — fail-safe WAIT")),
        (E.ORDER_PLACED,       Templates.order_placed(dry, "CPSEETF", 5, 91.65, 458.25, "ORD001", "MARKET")),
        (E.ORDER_FILLED,       Templates.order_filled(dry, "CPSEETF", 5, 91.65, 458.25, "ORD001", -0.23, 758.25, 741.75)),
        (E.ORDER_REJECTED,     Templates.order_rejected(dry, "SETFGOLD", "ORD002", "Insufficient funds")),
        (E.ORDER_UNFILLED,     Templates.order_unfilled(dry, "MODEFENCE", "ORD003", "timeout")),
        (E.BUDGET_LOW,         Templates.budget_low(dry, 1250, 1500, 83.3)),
        (E.BUDGET_EXHAUSTED,   Templates.budget_exhausted(dry, 1500)),
        (E.DAILY_SUMMARY,      Templates.daily_summary(
            dry,
            [{"symbol": "CPSEETF", "change_pct": -2.5, "triggered": True},
             {"symbol": "SETFGOLD", "change_pct": -0.3, "triggered": False}],
            nifty_pct=-0.8,
            triggered_syms=["CPSEETF"],
            orders=[{"symbol": "CPSEETF", "quantity": 5, "price": 91.65, "amount": 458.25}],
            budget_used=758.25, budget_total=1500,
        )),
        (E.NIFTY_FALL,         Templates.nifty_fall(dry, -2.3, "Nifty down 2.3%")),
        (E.LOGIN_FAILED,       Templates.error(dry, "LOGIN_FAILED", "generateSession returned status=False")),
        (E.BOT_STOPPED,        Templates.bot_stopped(dry, "manual stop (Ctrl+C)")),
    ]

    for event_type, text in msgs:
        test_text = f"🧪 <b>[TEST]</b>\n" + text
        logger.info(f"🧪 Sending test message: {event_type}")
        notifier.notify(event_type, test_text)
        time.sleep(0.3)  # small gap to avoid 429
