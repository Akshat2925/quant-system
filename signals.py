"""
BUY SIGNAL engine — Stage 9.

Computes buy signals for triggered ETFs in alert_only mode.
Includes full context: avg price, new avg after buy, P&L, portfolio weight,
funds check, NAV note, broker note.

Never places orders. Never raises.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

IST = ZoneInfo("Asia/Kolkata")


def _h(text) -> str:
    return html.escape(str(text))


@dataclass
class BuySignal:
    symbol:           str
    change_pct:       float
    trigger_pct:      float
    day_open:         float
    ltp:              float
    suggested_qty:    int
    limit_price:      float
    amount:           float
    qty_held:         float        # total units already held
    avg_price_held:   float        # current weighted avg price
    new_avg_price:    float        # new weighted avg after suggested buy
    invested_so_far:  float
    current_pnl_rs:   float | None
    current_pnl_pct:  float | None
    weight_before:    float | None
    weight_after:     float | None
    budget_remaining: float
    daily_cap:        float
    nav_note:         str
    funds_ok:         bool
    funds_shortfall:  float        # 0 if ok
    buy_broker:       str          # "angel" or "groww"
    message:          str          # formatted Telegram HTML


def compute(
    symbol:          str,
    change_pct:      float,
    trigger_pct:     float,
    day_open:        float,
    ltp:             float,
    allocation:      float,        # Rs amount allocated for this ETF
    portfolio_item,                # PortfolioItem or None
    total_portfolio_value: float | None,
    budget_remaining: float,
    daily_cap:        float,
    nav_note:         str,
    funds_available:  float | None,
    buy_broker:       str = "angel",
    dry_run:          bool = False,
    mode:             str = "alert_only",
) -> BuySignal | None:
    """
    Compute a BUY SIGNAL for one triggered ETF.
    Returns None if qty would be 0 (allocation too small).
    Never raises.
    """
    try:
        qty = int(allocation / ltp) if ltp > 0 else 0
        if qty <= 0:
            logger.info(f"📊 {symbol}: allocation Rs.{allocation:.0f} too small for 1 unit @ Rs.{ltp:.2f}")
            return None

        limit_price = round(ltp * 1.001, 2)
        amount      = round(qty * ltp, 2)

        # Current holdings context
        qty_held       = float(portfolio_item.qty_total)  if portfolio_item else 0.0
        avg_price_held = float(portfolio_item.avg_price)  if portfolio_item else 0.0
        invested       = float(portfolio_item.invested)   if portfolio_item else 0.0

        # New weighted average after buy
        new_total = qty_held + qty
        new_avg   = round(
            (avg_price_held * qty_held + ltp * qty) / new_total, 2
        ) if new_total > 0 else ltp

        # P&L on current holding
        pnl_rs  = round((ltp - avg_price_held) * qty_held, 2) if qty_held > 0 and avg_price_held > 0 else None
        pnl_pct = round(pnl_rs / invested * 100, 2) if (pnl_rs is not None and invested > 0) else None

        # Weights
        cur_value   = round(ltp * qty_held, 2) if qty_held > 0 else 0.0
        after_value = round(ltp * new_total, 2)
        total       = total_portfolio_value or 0.0
        w_before = round(cur_value   / total * 100, 2) if total > 0 else None
        w_after  = round(after_value / total * 100, 2) if total > 0 else None

        # Funds check
        funds_ok     = True
        shortfall    = 0.0
        if funds_available is not None and not dry_run and buy_broker == "angel":
            if funds_available < amount:
                funds_ok  = False
                shortfall = round(amount - funds_available, 2)

        # Mode tag
        mode_tags = {
            "alert_only": "📊 <b>[ALERT ONLY]</b>",
            "dry_run":    "🧪 <b>[DRY RUN]</b>",
            "live":       "💰 <b>[LIVE]</b>",
        }
        mode_tag = mode_tags.get(mode, "📊 <b>[ALERT ONLY]</b>")

        # Format message
        lines = [
            f"📊 <b>BUY SIGNAL</b>  {mode_tag}",
            f"<b>{_h(symbol)}</b>",
            f"",
            f"Fall:         {change_pct:+.2f}%  (trigger {trigger_pct:+.1f}%)",
            f"Day open:     Rs.{day_open:.2f}",
            f"Current LTP:  Rs.{ltp:.2f}",
            f"",
            f"<b>Suggested order</b>",
            f"Qty:          {qty} units",
            f"LIMIT price:  Rs.{limit_price}",
            f"Amount:       Rs.{amount:.2f}",
            f"",
            f"<b>Your holding</b>",
            f"Units held:   {qty_held:.0f}  (Groww + Angel)",
            f"Avg price:    Rs.{avg_price_held:.2f}" if avg_price_held else "Avg price:    N/A (not held)",
        ]
        if pnl_rs is not None:
            lines.append(f"Unrealised:   Rs.{pnl_rs:+.2f} ({pnl_pct:+.2f}%)")
        lines += [
            f"New avg:      Rs.{new_avg:.2f} (after this buy)",
            f"",
            f"<b>Budget</b>",
            f"Remaining:    Rs.{budget_remaining:.0f}",
            f"Today cap:    Rs.{daily_cap:.0f}",
        ]
        if w_before is not None:
            lines.append(f"Weight:       {w_before:.1f}% → {w_after:.1f}% (after buy)")
        lines += [
            f"",
            f"<b>NAV</b>: {_h(nav_note)}",
        ]
        if not funds_ok:
            lines += [
                f"",
                f"⚠️ <b>Insufficient funds</b>",
                f"Need Rs.{amount:.2f}, have Rs.{funds_available:.2f}",
                f"Shortfall: Rs.{shortfall:.2f}",
            ]
        lines += [
            f"",
            f"Units bought on <b>{_h(buy_broker.title())}</b> sit in the {_h(buy_broker.title())} demat.",
            f"<i>This is an alert — no order was placed.</i>",
        ]

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3980] + "\n…<i>(truncated)</i>"

        return BuySignal(
            symbol=symbol, change_pct=change_pct, trigger_pct=trigger_pct,
            day_open=day_open, ltp=ltp, suggested_qty=qty,
            limit_price=limit_price, amount=amount,
            qty_held=qty_held, avg_price_held=avg_price_held,
            new_avg_price=new_avg, invested_so_far=invested,
            current_pnl_rs=pnl_rs, current_pnl_pct=pnl_pct,
            weight_before=w_before, weight_after=w_after,
            budget_remaining=budget_remaining, daily_cap=daily_cap,
            nav_note=nav_note, funds_ok=funds_ok,
            funds_shortfall=shortfall, buy_broker=buy_broker, message=msg,
        )
    except Exception as e:
        logger.warning(f"⚠️ Signal compute error for {symbol}: {e}")
        return None
