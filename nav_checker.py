"""
NAV Checker â€” premium/discount detection before each ETF buy.

Data source: mfapi.in returns the most recently declared NAV (end-of-day
value, typically published after 9 PM).  This is NOT a live iNAV feed.
On a dip day the market price is often BELOW the previous day's NAV,
making the premium appear negative ("discount").  That is expected and
is still a valid entry signal â€” we are comparing against a close-enough
reference, not a real-time iNAV.

If live iNAV is needed in the future it can be sourced from a separate
data provider (e.g. AMC websites or NSE iNAV feed) and plugged into
get_nav() without changing the rest of this file.

Premium bands (configurable per-ETF via max_premium in ETF_LIST)
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  Negative (discount)        â†’ BUY  (market order)
  0 % to +max_premium        â†’ BUY  (market order)
  +max_premium to +1 %       â†’ LIMIT (price above ltp to ensure fill)
  +1 % to +2 %               â†’ WAIT (skip this cycle, retry next)
  > +2 %                     â†’ SKIP (do not buy at all today)

Mode
â”€â”€â”€â”€
  "advisory" (default) â€” log premium, only hard-block above 2 %.
                          The per-ETF max_premium still triggers LIMIT orders.
  "strict"             â€” honour the per-ETF max_premium as a WAIT threshold.

NAV staleness guard: if the NAV date returned by mfapi.in is older than
3 calendar days, treat it as unavailable (fail-safe WAIT).
"""

import requests
from datetime import date, datetime, timedelta
from loguru import logger
from dataclasses import dataclass

# â”€â”€ ETF universe â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Angel instrument master uses -EQ suffix for NSE cash segment ETFs.
# Bare symbols (ICICISILVER etc.) return no data from ltpData.
# Tokens verified against OpenAPIScripMaster.json on 2026-09-30.
ETF_LIST = {
    "SILVERIETF-EQ": {
        "token":       "7942",
        "exchange":    "NSE",
        "full_name":   "ICICI Prudential Silver ETF",
        "nav_url":     "https://api.mfapi.in/mf/149280",
        "is_silver":   True,
        "trigger_pct": -6.5,
        "fixed_amt":   350,
        "max_premium": 0.5,
    },
    "SETFGOLD-EQ": {
        "token":       "17272",
        "exchange":    "NSE",
        "full_name":   "SBI Gold ETF",
        "nav_url":     "https://api.mfapi.in/mf/102792",
        "is_silver":   False,
        "trigger_pct": -2.0,
        "fixed_amt":   None,
        "max_premium": 0.3,
    },
    "MODEFENCE-EQ": {
        "token":       "24944",
        "exchange":    "NSE",
        "full_name":   "Motilal Oswal Nifty India Defence ETF",
        "nav_url":     "https://api.mfapi.in/mf/151269",
        "is_silver":   False,
        "trigger_pct": -3.0,
        "fixed_amt":   None,
        "max_premium": 0.5,
    },
    "CPSEETF-EQ": {
        "token":       "2328",
        "exchange":    "NSE",
        "full_name":   "CPSE ETF",
        "nav_url":     "https://api.mfapi.in/mf/118825",
        "is_silver":   False,
        "trigger_pct": -2.0,
        "fixed_amt":   None,
        "max_premium": 0.5,
    },
    "METALIETF-EQ": {
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

NAV_STALE_DAYS = 3   # if NAV is older than this many days, treat as unavailable


# â”€â”€ Result dataclass â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@dataclass
class NAVResult:
    symbol:        str
    market_price:  float
    nav:           float
    nav_date:      str          # "YYYY-MM-DD" of the NAV used, or ""
    premium_pct:   float
    is_good_entry: bool
    use_limit:     bool
    reason:        str
    action:        str          # "BUY" | "LIMIT" | "WAIT" | "SKIP"


# â”€â”€ Checker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class NAVChecker:
    """NAV-based premium checker for ETF buy decisions."""

    def get_nav(self, symbol: str) -> tuple[float | None, str]:
        """Fetch latest NAV from mfapi.in.

        Returns (nav_value, nav_date_str) or (None, "") on failure.
        Treats NAVs older than NAV_STALE_DAYS as unavailable.
        """
        try:
            url  = ETF_LIST[symbol]["nav_url"]
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data     = resp.json()
            entry    = data["data"][0]
            nav      = float(entry["nav"])
            nav_date = entry.get("date", "")           # "DD-MM-YYYY" from mfapi

            # Parse and check staleness
            try:
                nav_dt = datetime.strptime(nav_date, "%d-%m-%Y").date()
                age    = (date.today() - nav_dt).days
                if age > NAV_STALE_DAYS:
                    logger.warning(
                        f"âš ï¸ {symbol} NAV date {nav_date} is {age} days old "
                        f"(limit {NAV_STALE_DAYS}) â€” treating as unavailable"
                    )
                    return None, nav_date
            except ValueError:
                logger.warning(f"âš ï¸ {symbol} NAV date unparseable: {nav_date!r}")

            nav_iso = nav_dt.isoformat() if "nav_dt" in dir() else nav_date
            logger.info(f"ðŸ“Š {symbol} NAV = â‚¹{nav:.4f} (date: {nav_date}) "
                        f"[NOTE: last declared NAV, not live iNAV]")
            return nav, nav_iso

        except Exception as e:
            logger.warning(f"âš ï¸ NAV unavailable for {symbol}: {e}")
            return None, ""

    def check(self, symbol: str, market_price: float,
              mode: str = "advisory") -> NAVResult:
        """Compute premium and decide action.

        mode:
          "advisory" â€” only hard-blocks above 2 %; per-ETF max_premium
                       still used to trigger LIMIT orders.
          "strict"   â€” per-ETF max_premium is the WAIT threshold.
        """
        nav, nav_date = self.get_nav(symbol)
        max_premium   = ETF_LIST.get(symbol, {}).get("max_premium", 0.5)

        if nav is None or nav == 0:
            return NAVResult(
                symbol=symbol, market_price=market_price, nav=0,
                nav_date=nav_date, premium_pct=0,
                is_good_entry=False, use_limit=False,
                reason="âš ï¸ NAV unavailable â€” waiting for feed (fail-safe)",
                action="WAIT",
            )

        premium_pct = ((market_price / nav) - 1) * 100

        # â”€â”€ Premium band logic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if premium_pct < 0:
            action, is_good, use_limit = "BUY", True, False
            reason = f"âœ… Discount {abs(premium_pct):.2f}% below last NAV"

        elif premium_pct <= max_premium:
            action, is_good, use_limit = "BUY", True, False
            reason = f"âœ… Premium {premium_pct:.2f}% â‰¤ max {max_premium}%"

        elif premium_pct <= 1.0:
            # Between max_premium and 1 % â†’ LIMIT order in advisory;
            # WAIT in strict mode (honours the per-ETF threshold).
            if mode == "strict":
                action, is_good, use_limit = "WAIT", False, False
                reason = (f"âŒ Premium {premium_pct:.2f}% > max {max_premium}% "
                          f"(strict mode â€” waiting)")
            else:
                action, is_good, use_limit = "LIMIT", True, True
                reason = f"âš ï¸ Premium {premium_pct:.2f}% â€” using limit order"

        elif premium_pct <= 2.0:
            action, is_good, use_limit = "WAIT", False, False
            reason = f"âŒ Premium {premium_pct:.2f}% â€” waiting this cycle"

        else:
            action, is_good, use_limit = "SKIP", False, False
            reason = f"ðŸš« Premium {premium_pct:.2f}% > 2% â€” skipping entirely"

        logger.info(
            f"NAV [{symbol}] market â‚¹{market_price:.2f} / "
            f"NAV â‚¹{nav:.2f} ({nav_date}) = {premium_pct:+.2f}% â†’ {action} "
            f"[mode: {mode}]"
        )

        return NAVResult(
            symbol=symbol, market_price=market_price, nav=nav,
            nav_date=nav_date, premium_pct=round(premium_pct, 4),
            is_good_entry=is_good, use_limit=use_limit,
            reason=reason, action=action,
        )

    def best_etf_to_buy(self, connector, mode: str = "advisory") -> str | None:
        """Return the symbol with the lowest premium that is still buyable."""
        results = []
        for sym, info in ETF_LIST.items():
            ltp = connector.get_ltp(info["exchange"], sym, info["token"])
            if ltp:
                r = self.check(sym, ltp, mode=mode)
                if r.is_good_entry:
                    results.append(r)
        if not results:
            logger.warning("âš ï¸ No ETF at acceptable premium")
            return None
        results.sort(key=lambda x: x.premium_pct)
        best = results[0]
        logger.success(f"âœ… Best ETF: {best.symbol} ({best.reason})")
        return best.symbol
