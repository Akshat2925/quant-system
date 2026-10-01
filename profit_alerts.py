"""
Sell / profit-booking alerts — Stage 11.

Fires once per level at configurable gain thresholds (e.g. +8%, +12%, +20%).
Persists state so restarts do not resend.
Re-arms if price falls back below level minus 2%.
NEVER places a sell order.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from zoneinfo import ZoneInfo

from loguru import logger

IST = ZoneInfo("Asia/Kolkata")

ALERT_STATE_FILE = "profit_alert_state.json"

# Default profit alert levels (% gain over weighted avg price)
DEFAULT_LEVELS = [8.0, 12.0, 20.0]
REARM_BUFFER   = 2.0   # re-arm when price drops below level - 2%


def _atomic_write(path: str, data: dict) -> None:
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".tmp_pa_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _load_state() -> dict:
    try:
        with open(ALERT_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    _atomic_write(ALERT_STATE_FILE, state)


def check(portfolio_items: list,
          notifier=None,
          levels: list[float] | None = None) -> list[dict]:
    """
    Check each portfolio item for profit alert levels.
    Returns list of fired alert dicts.
    Never raises.
    """
    if levels is None:
        levels = DEFAULT_LEVELS

    state  = _load_state()
    fired  = []

    try:
        for item in portfolio_items:
            sym      = item.symbol if hasattr(item, "symbol") else item.get("symbol", "")
            avg      = item.avg_price if hasattr(item, "avg_price") else item.get("avg_price", 0)
            ltp      = item.current_price if hasattr(item, "current_price") else item.get("current_price")
            qty_g    = item.qty_groww if hasattr(item, "qty_groww") else item.get("qty_groww", 0)
            qty_a    = item.qty_angel if hasattr(item, "qty_angel") else item.get("qty_angel", 0)

            if not ltp or not avg or avg <= 0:
                continue

            gain_pct = (ltp - avg) / avg * 100
            sym_state = state.get(sym, {})

            for level in sorted(levels):
                key = f"level_{level}"
                fired_flag = sym_state.get(key, False)

                if gain_pct >= level and not fired_flag:
                    # Fire alert
                    gain_rs  = round((ltp - avg) * (qty_g + qty_a), 2)
                    half_g   = int(qty_g // 2)
                    half_a   = int(qty_a // 2)

                    # Tax note
                    tax_note = (
                        "Short-term capital gains (STCG) at 20% if held < 1 year; "
                        "LTCG at 12.5% if held > 1 year."
                    )

                    msg = (
                        f"💰 <b>PROFIT ALERT +{level:.0f}%</b>\n"
                        f"<b>{sym}</b>\n"
                        f"\n"
                        f"Avg price:    Rs.{avg:.2f}\n"
                        f"Current LTP:  Rs.{ltp:.2f}\n"
                        f"Gain:         +{gain_pct:.1f}%  (Rs.{gain_rs:+.2f})\n"
                        f"\n"
                        f"<b>Holdings</b>\n"
                        f"Groww:  {qty_g:.0f} units\n"
                        f"Angel:  {qty_a:.0f} units\n"
                        f"\n"
                        f"<b>Suggested partial booking</b>\n"
                    )
                    if half_g > 0:
                        msg += f"Sell ~{half_g} units on Groww (units held there)\n"
                    if half_a > 0:
                        msg += f"Sell ~{half_a} units on Angel (units held there)\n"
                    msg += (
                        f"\n⚠️ A sell must be placed at the broker holding those units.\n"
                        f"\n📋 Tax: {tax_note}\n"
                        f"\n<i>This is an alert — no sell order was placed.</i>"
                    )

                    if len(msg) > 4000:
                        msg = msg[:3980] + "\n…"

                    logger.info(f"💰 Profit alert fired: {sym} +{level:.0f}%")
                    if notifier:
                        try:
                            notifier.notify(f"PROFIT_ALERT_{level:.0f}", msg)
                        except Exception:
                            pass

                    sym_state[key] = True
                    fired.append({"symbol": sym, "level": level, "gain_pct": round(gain_pct, 2)})

                elif gain_pct < (level - REARM_BUFFER) and fired_flag:
                    # Re-arm
                    sym_state[key] = False
                    logger.info(f"🔄 Profit alert re-armed: {sym} level {level}%")

            state[sym] = sym_state

        _save_state(state)

    except Exception as e:
        logger.warning(f"⚠️ profit_alerts.check error: {e}")

    return fired
