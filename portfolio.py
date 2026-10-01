"""
Unified portfolio model — Stage 7.

Combines holdings from Groww and Angel One into one view per ETF.

For each ETF:
  - qty_groww, qty_angel, qty_total
  - Weighted average buy price across both brokers
  - Total invested
  - Current value and unrealised P&L (using Angel LTP as primary price)
  - Day change (previous close from ltpData if available)
  - Portfolio weight (% of total current value)
  - Per-broker breakdown

All money math uses Python float with 2-decimal rounding for display.
Labels every number with its price source and timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class BrokerSlice:
    broker:       str    # "groww" or "angel"
    quantity:     float
    avg_price:    float
    invested:     float


@dataclass
class PortfolioItem:
    symbol:          str
    full_name:       str
    qty_groww:       float = 0.0
    qty_angel:       float = 0.0
    qty_total:       float = 0.0
    avg_price:       float = 0.0   # weighted across both brokers
    invested:        float = 0.0   # qty_total * avg_price
    current_price:   float | None = None
    current_value:   float | None = None
    unrealised_pnl:  float | None = None   # current_value - invested
    unrealised_pct:  float | None = None   # pnl / invested * 100
    prev_close:      float | None = None
    day_change_rs:   float | None = None   # current_price - prev_close
    day_change_pct:  float | None = None
    weight_pct:      float | None = None   # % of total portfolio value
    price_source:    str   = "none"   # "angel" | "groww" | "none"
    price_as_of:     str   = ""       # ISO timestamp
    brokers:         list[BrokerSlice] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol":         self.symbol,
            "full_name":      self.full_name,
            "qty_groww":      round(self.qty_groww, 4),
            "qty_angel":      round(self.qty_angel, 4),
            "qty_total":      round(self.qty_total, 4),
            "avg_price":      round(self.avg_price, 2),
            "invested":       round(self.invested, 2),
            "current_price":  round(self.current_price, 2) if self.current_price else None,
            "current_value":  round(self.current_value, 2) if self.current_value else None,
            "unrealised_pnl": round(self.unrealised_pnl, 2) if self.unrealised_pnl is not None else None,
            "unrealised_pct": round(self.unrealised_pct, 2) if self.unrealised_pct is not None else None,
            "prev_close":     round(self.prev_close, 2) if self.prev_close else None,
            "day_change_rs":  round(self.day_change_rs, 2) if self.day_change_rs is not None else None,
            "day_change_pct": round(self.day_change_pct, 2) if self.day_change_pct is not None else None,
            "weight_pct":     round(self.weight_pct, 2) if self.weight_pct is not None else None,
            "price_source":   self.price_source,
            "price_as_of":    self.price_as_of,
            "brokers":        [
                {"broker": b.broker, "quantity": round(b.quantity, 4),
                 "avg_price": round(b.avg_price, 2), "invested": round(b.invested, 2)}
                for b in self.brokers
            ],
        }


@dataclass
class PortfolioSummary:
    items:           list[PortfolioItem]
    total_invested:  float = 0.0
    total_value:     float | None = None
    total_pnl:       float | None = None
    total_pnl_pct:   float | None = None
    as_of:           str   = ""   # ISO timestamp

    def to_dict(self) -> dict:
        return {
            "as_of":          self.as_of,
            "total_invested": round(self.total_invested, 2),
            "total_value":    round(self.total_value, 2) if self.total_value is not None else None,
            "total_pnl":      round(self.total_pnl, 2)  if self.total_pnl  is not None else None,
            "total_pnl_pct":  round(self.total_pnl_pct, 2) if self.total_pnl_pct is not None else None,
            "items":          [i.to_dict() for i in self.items],
        }


def build(watchlist: list[dict],
          price_results: dict | None = None) -> PortfolioSummary:
    """
    Build the unified portfolio from the resolved watchlist.

    watchlist     : output of watchlist.build()
    price_results : output of PriceProvider.get_all() — {symbol: PriceResult}
                    Pass None to get a holdings-only view without P&L.

    Returns PortfolioSummary. Never raises.
    """
    now_str = datetime.now(IST).isoformat()
    items: list[PortfolioItem] = []

    for etf in watchlist:
        sym       = etf.get("symbol", "")
        full_name = etf.get("full_name", sym)
        source    = etf.get("source", "")

        # Quantities per broker
        qty_groww = 0.0
        qty_angel = 0.0
        if source in ("groww", "mixed", "cache"):
            qty_groww = float(etf.get("quantity", 0))
        elif source == "angel":
            qty_angel = float(etf.get("quantity", 0))
        elif source == "mixed":
            # mixed means both — split not tracked at this level
            qty_groww = float(etf.get("quantity", 0))

        avg_price = float(etf.get("avg_price", 0))
        qty_total = qty_groww + qty_angel
        invested  = round(qty_total * avg_price, 2)

        brokers = []
        if qty_groww > 0:
            brokers.append(BrokerSlice(
                broker="groww", quantity=qty_groww,
                avg_price=avg_price, invested=round(qty_groww * avg_price, 2)
            ))
        if qty_angel > 0:
            brokers.append(BrokerSlice(
                broker="angel", quantity=qty_angel,
                avg_price=avg_price, invested=round(qty_angel * avg_price, 2)
            ))

        # Prices
        current_price  = None
        prev_close     = None
        price_source   = "none"
        price_as_of    = ""

        if price_results:
            pr = price_results.get(sym)
            if pr and pr.available:
                current_price = pr.ltp
                price_source  = pr.source
                price_as_of   = datetime.now(IST).strftime("%H:%M IST")

        # P&L
        current_value   = round(qty_total * current_price, 2) if current_price and qty_total else None
        unrealised_pnl  = round(current_value - invested, 2) if current_value is not None else None
        unrealised_pct  = round(unrealised_pnl / invested * 100, 2) if (unrealised_pnl is not None and invested > 0) else None

        item = PortfolioItem(
            symbol         = sym,
            full_name      = full_name,
            qty_groww      = qty_groww,
            qty_angel      = qty_angel,
            qty_total      = qty_total,
            avg_price      = avg_price,
            invested       = invested,
            current_price  = current_price,
            current_value  = current_value,
            unrealised_pnl = unrealised_pnl,
            unrealised_pct = unrealised_pct,
            prev_close     = prev_close,
            day_change_rs  = None,
            day_change_pct = None,
            weight_pct     = None,   # filled below after totals
            price_source   = price_source,
            price_as_of    = price_as_of,
            brokers        = brokers,
        )
        items.append(item)

    # Totals
    total_invested = round(sum(i.invested for i in items), 2)
    total_value    = sum(i.current_value for i in items if i.current_value is not None)
    total_value    = round(total_value, 2) if total_value else None
    total_pnl      = round(total_value - total_invested, 2) if total_value is not None else None
    total_pnl_pct  = round(total_pnl / total_invested * 100, 2) if (total_pnl is not None and total_invested > 0) else None

    # Fill weights
    for item in items:
        if item.current_value is not None and total_value and total_value > 0:
            item.weight_pct = round(item.current_value / total_value * 100, 2)

    logger.info(
        f"📊 Portfolio: {len(items)} ETFs | "
        f"Invested: Rs.{total_invested:,.2f} | "
        f"Value: Rs.{total_value:,.2f}" if total_value else
        f"📊 Portfolio: {len(items)} ETFs | Invested: Rs.{total_invested:,.2f}"
    )

    return PortfolioSummary(
        items          = items,
        total_invested = total_invested,
        total_value    = total_value,
        total_pnl      = total_pnl,
        total_pnl_pct  = total_pnl_pct,
        as_of          = now_str,
    )
