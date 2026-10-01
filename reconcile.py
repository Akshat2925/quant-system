"""
Purchase and sale reconciliation — Stage 10.

On every holdings sync, diffs quantities against holdings_snapshot.json.
Qty increase = purchase; qty decrease = sale.
Updates monthly budget and daily state for purchases detected.
First snapshot is never treated as a purchase.
Supports /bought SYMBOL QTY PRICE manual command.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

IST              = ZoneInfo("Asia/Kolkata")
SNAPSHOT_FILE    = "holdings_snapshot.json"


def _atomic_write(path: str, data: dict) -> None:
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".tmp_snap_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _load_snapshot() -> dict:
    try:
        with open(SNAPSHOT_FILE) as f:
            data = json.load(f)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_snapshot(holdings: list[dict]) -> None:
    snap = {
        "saved_at": datetime.now(IST).isoformat(),
        "first":    False,
        "holdings": {
            h.get("symbol", h.get("trading_symbol", "")): {
                "quantity":    float(h.get("qty_total", h.get("quantity", 0))),
                "avg_price":   float(h.get("avg_price", h.get("average_price", 0))),
            }
            for h in holdings if h.get("symbol") or h.get("trading_symbol")
        }
    }
    _atomic_write(SNAPSHOT_FILE, snap)


# Events are plain dicts: {"type": "purchase"|"sale", "symbol", "qty_change", "broker", "est_amount"}


def diff(current_holdings: list[dict],
         notifier=None) -> list[dict]:
    """
    Compare current holdings against snapshot.
    Returns list of event dicts: {"type": "purchase"|"sale", "symbol", "qty_change", "broker", "est_amount"}
    First call (no snapshot) saves snapshot and returns [].
    Never raises.
    """
    events = []
    try:
        snap = _load_snapshot()

        if not snap or snap.get("first", True):
            # First snapshot — just save, treat nothing as a purchase
            _save_snapshot(current_holdings)
            logger.info("📸 Holdings snapshot created (first run — no purchases attributed)")
            return []

        snap_holdings = snap.get("holdings", {})

        for h in current_holdings:
            sym      = h.get("symbol", h.get("trading_symbol", ""))
            qty_now  = float(h.get("qty_total", h.get("quantity", 0)))
            broker   = h.get("source", "unknown")
            prev     = snap_holdings.get(sym, {})
            qty_prev = float(prev.get("quantity", 0)) if prev else 0.0
            diff_qty = qty_now - qty_prev

            if diff_qty > 0.5:   # purchase (allow small float noise)
                ltp      = h.get("current_price") or h.get("avg_price") or 0
                est_amt  = round(diff_qty * ltp, 2) if ltp else None
                event = {
                    "type":       "purchase",
                    "symbol":     sym,
                    "qty_change": round(diff_qty, 4),
                    "broker":     broker,
                    "est_amount": est_amt,
                }
                events.append(event)
                msg = (
                    f"🛒 <b>Purchase detected</b> on <b>{broker.title()}</b>\n"
                    f"<b>{sym}</b>  +{diff_qty:.0f} units"
                    + (f"  ≈ Rs.{est_amt:.2f}" if est_amt else "")
                )
                logger.info(msg)
                if notifier:
                    try:
                        notifier.notify("PURCHASE_DETECTED", msg)
                    except Exception:
                        pass

            elif diff_qty < -0.5:   # sale
                event = {
                    "type":       "sale",
                    "symbol":     sym,
                    "qty_change": round(diff_qty, 4),
                    "broker":     broker,
                    "est_amount": None,
                }
                events.append(event)
                msg = (
                    f"📤 <b>Sale detected</b> on <b>{broker.title()}</b>\n"
                    f"<b>{sym}</b>  {diff_qty:.0f} units"
                )
                logger.info(msg)
                if notifier:
                    try:
                        notifier.notify("SALE_DETECTED", msg)
                    except Exception:
                        pass

        # Update snapshot
        _save_snapshot(current_holdings)

    except Exception as e:
        logger.warning(f"⚠️ Reconcile error: {e}")

    return events


def handle_manual_bought(symbol: str, qty: float, price: float,
                         notifier=None) -> dict:
    """
    Handle /bought SYMBOL QTY PRICE Telegram command.
    Returns event dict. Never raises.
    """
    try:
        amount = round(qty * price, 2)
        event = {
            "type":       "purchase",
            "symbol":     symbol,
            "qty_change": qty,
            "broker":     "manual",
            "est_amount": amount,
        }
        msg = (
            f"✅ <b>Manual purchase recorded</b>\n"
            f"<b>{symbol}</b>  {qty:.0f} units @ Rs.{price:.2f}\n"
            f"Amount: Rs.{amount:.2f}"
        )
        logger.info(msg)
        if notifier:
            try:
                notifier.notify("PURCHASE_DETECTED", msg)
            except Exception:
                pass
        return event
    except Exception as e:
        logger.warning(f"⚠️ handle_manual_bought error: {e}")
        return {}
