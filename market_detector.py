"""
Market-wide fall detector for Nifty 50.

Used by bot.py as an ADVISORY-only component — it sends a Telegram alert
when Nifty is down 2 % or more but does NOT trigger extra spending.

The day-open price comes directly from ltpData() (same as ETF quotes),
so there is no candle-data dependency and no estimated-open-price path.
"""

from loguru import logger
from dataclasses import dataclass

NIFTY_TOKEN    = "99926000"
NIFTY_SYMBOL   = "Nifty 50"
NIFTY_EXCHANGE = "NSE"


@dataclass
class MarketStatus:
    current_price: float
    open_price:    float
    change_pct:    float
    trigger_level: int        # 0 = normal, 1/2/3 = increasing severity
    accumulation:  bool
    should_buy:    bool       # kept for API compatibility; bot.py ignores this
    reason:        str


class MarketFallDetector:
    def __init__(self, trigger1: float = -2.0,
                 trigger2: float = -4.0,
                 trigger3: float = -6.0,
                 acc_days: int   = 5):
        self.trigger1  = trigger1
        self.trigger2  = trigger2
        self.trigger3  = trigger3
        self.acc_days  = acc_days
        self._history: list[float] = []

    def check(self, connector) -> MarketStatus:
        """Check current Nifty level.

        Uses get_quote() which returns (ltp, day_open) from ltpData — the
        same reliable source used for ETFs.  If either value is unavailable
        (before 9:15 or API failure), returns a neutral status with
        should_buy=False so callers always get a safe result.
        """
        ltp, open_price = connector.get_quote(NIFTY_EXCHANGE, NIFTY_SYMBOL, NIFTY_TOKEN)

        if ltp is None or open_price is None:
            logger.warning("⚠️ Nifty quote unavailable — skipping market check this cycle")
            return MarketStatus(0, 0, 0.0, 0, False, False, "Nifty data unavailable")

        pct = ((ltp - open_price) / open_price) * 100

        logger.info(
            f"📈 Nifty: ₹{ltp:.2f} | Open: ₹{open_price:.2f} | Change: {pct:+.2f}%"
        )

        if pct <= self.trigger3:
            level = 3
        elif pct <= self.trigger2:
            level = 2
        elif pct <= self.trigger1:
            level = 1
        else:
            level = 0

        acc        = self._check_accumulation()
        should_buy = level > 0 or (acc and pct < -1.0)

        if level == 3:
            reason = f"🚨 MAJOR Nifty fall {pct:.2f}%"
        elif level == 2:
            reason = f"🔴 Big Nifty fall {pct:.2f}%"
        elif level == 1:
            reason = f"🟠 Nifty down {pct:.2f}%"
        elif acc:
            reason = f"📉 Nifty accumulation mode — down {abs(pct):.2f}% today"
        else:
            reason = f"✅ Nifty normal {pct:+.2f}%"

        logger.info(reason)
        return MarketStatus(ltp, open_price, round(pct, 4),
                            level, acc, should_buy, reason)

    def update_history(self, pct: float) -> None:
        """Record today's Nifty return for the accumulation check."""
        self._history.append(pct)
        if len(self._history) > 10:
            self._history.pop(0)

    def _check_accumulation(self) -> bool:
        if len(self._history) < self.acc_days:
            return False
        return all(x < 0 for x in self._history[-self.acc_days:])
