"""NAV Checker — SEBI guideline based premium/discount detection.

SEBI Rule (Market Price / iNAV - 1):
  Negative (discount)  → ✅ Best entry
  0% to +0.5%          → ✅ Good — buy
  +0.5% to +1%         → ⚠️  Use limit order
  +1% to +2%           → ❌ Wait
  > +2%                → 🚫 Skip completely
"""

import requests
from loguru import logger
from dataclasses import dataclass

# ETF List — exact Angel One symbols
ETF_LIST = {
    "ICICISILVER": {
        "token":       "7942",
        "exchange":    "NSE",
        "full_name":   "ICICI Prudential Silver ETF",
        "nav_url":     "https://api.mfapi.in/mf/149280",
        "is_silver":   True,
        "trigger_pct": -6.5,
        "fixed_amt":   350,
        "max_premium": 0.5,
    },
    "SETFGOLD": {
        "token":       "17272",
        "exchange":    "NSE",
        "full_name":   "SBI Gold ETF",
        "nav_url":     "https://api.mfapi.in/mf/102792",
        "is_silver":   False,
        "trigger_pct": -2.0,
        "fixed_amt":   None,
        "max_premium": 0.3,
    },
    "MODEFENCE": {
        "token":       "24944",
        "exchange":    "NSE",
        "full_name":   "Motilal Oswal Nifty India Defence ETF",
        "nav_url":     "https://api.mfapi.in/mf/151269",
        "is_silver":   False,
        "trigger_pct": -3.0,
        "fixed_amt":   None,
        "max_premium": 0.5,
    },
    "CPSEETF": {
        "token":       "2328",
        "exchange":    "NSE",
        "full_name":   "CPSE ETF",
        "nav_url":     "https://api.mfapi.in/mf/118825",
        "is_silver":   False,
        "trigger_pct": -2.0,
        "fixed_amt":   None,
        "max_premium": 0.5,
    },
    "METALIETF": {
        "token":       "24861",
        "exchange":    "NSE",
        "full_name":   "ICICI Prudential Nifty Metal ETF",
        "nav_url":     "https://api.mfapi.in/mf/118989",
        "is_silver":   False,
        "trigger_pct": -2.0,
        "fixed_amt":   None,
        "max_premium": 0.5,
    },
}


@dataclass
class NAVResult:
    symbol:        str
    market_price:  float
    nav:           float
    premium_pct:   float
    is_good_entry: bool
    use_limit:     bool    # True = use limit order instead of market
    reason:        str
    action:        str     # BUY / LIMIT / WAIT / SKIP


class NAVChecker:
    """SEBI guideline based NAV checker."""

    def get_nav(self, symbol: str) -> float | None:
        try:
            url  = ETF_LIST[symbol]["nav_url"]
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            nav  = float(data["data"][0]["nav"])
            logger.info(f"📊 {symbol} NAV = ₹{nav:.4f}")
            return nav
        except Exception as e:
            # NOTE: when NAV is unavailable, check() below defaults to allowing
            # the buy. That is a deliberate but risky tradeoff (see check()) —
            # logged at WARNING (not INFO) so it's visible, not buried.
            logger.warning(f"⚠️ NAV unavailable for {symbol}: {e}")
            return None

    def check(self, symbol: str, market_price: float) -> NAVResult:
        nav = self.get_nav(symbol)

        if nav is None or nav == 0:
            # CHANGED from the original: previously defaulted to BUY when the
            # NAV feed was unreachable, which silently disables the entire
            # premium-safety check whenever mfapi.in has a hiccup. Failing
            # safe (WAIT, retry next cycle) is the correct default for
            # anything placing real orders — a missed buy window costs you
            # nothing; buying at an unknown premium can cost real money.
            return NAVResult(
                symbol=symbol, market_price=market_price, nav=0,
                premium_pct=0, is_good_entry=False, use_limit=False,
                reason="⚠️ NAV unavailable — waiting for feed to recover (fail-safe)",
                action="WAIT"
            )

        # SEBI formula: (Market Price / NAV - 1) * 100
        premium_pct = ((market_price / nav) - 1) * 100

        # Apply SEBI rules
        if premium_pct < 0:
            action     = "BUY"
            is_good    = True
            use_limit  = False
            reason     = f"✅ DISCOUNT {abs(premium_pct):.2f}% — Best entry!"

        elif premium_pct <= 0.5:
            action     = "BUY"
            is_good    = True
            use_limit  = False
            reason     = f"✅ Premium {premium_pct:.2f}% — Good entry"

        elif premium_pct <= 1.0:
            action     = "LIMIT"
            is_good    = True
            use_limit  = True
            reason     = f"⚠️ Premium {premium_pct:.2f}% — Use limit order"

        elif premium_pct <= 2.0:
            action     = "WAIT"
            is_good    = False
            use_limit  = False
            reason     = f"❌ Premium {premium_pct:.2f}% — Wait karo"

        else:
            action     = "SKIP"
            is_good    = False
            use_limit  = False
            reason     = f"🚫 Premium {premium_pct:.2f}% > 2% — SKIP completely"

        logger.info(f"NAV [{symbol}] ₹{market_price:.2f} / NAV ₹{nav:.2f} = {premium_pct:+.2f}% → {action}")

        return NAVResult(
            symbol=symbol, market_price=market_price, nav=nav,
            premium_pct=round(premium_pct, 4), is_good_entry=is_good,
            use_limit=use_limit, reason=reason, action=action
        )

    def best_etf_to_buy(self, connector) -> str | None:
        """Find ETF with lowest premium right now."""
        results = []
        for sym, info in ETF_LIST.items():
            ltp = connector.get_ltp(info["exchange"], sym, info["token"])
            if ltp:
                r = self.check(sym, ltp)
                if r.is_good_entry:
                    results.append(r)

        if not results:
            logger.warning("⚠️ No ETF at acceptable premium")
            return None

        # Sort by premium — lowest first
        results.sort(key=lambda x: x.premium_pct)
        best = results[0]
        logger.success(f"✅ Best ETF: {best.symbol} ({best.reason})")
        return best.symbol
