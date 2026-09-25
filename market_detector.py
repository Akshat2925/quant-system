import os
from loguru import logger
from dataclasses import dataclass
from datetime import datetime

NIFTY_TOKEN    = "99926000"
NIFTY_SYMBOL   = "Nifty 50"
NIFTY_EXCHANGE = "NSE"


@dataclass
class MarketStatus:
    current_price: float
    open_price:    float
    change_pct:    float
    trigger_level: int
    accumulation:  bool
    should_buy:    bool
    reason:        str
    open_is_estimated: bool = False  # True if open_price is a guess, not real data


class MarketFallDetector:
    def __init__(self, trigger1=-2.0, trigger2=-4.0, trigger3=-6.0, acc_days=5):
        self.trigger1 = trigger1
        self.trigger2 = trigger2
        self.trigger3 = trigger3
        self.acc_days = acc_days
        self._history = []

    def check(self, connector):
        ltp = connector.get_ltp(NIFTY_EXCHANGE, NIFTY_SYMBOL, NIFTY_TOKEN)
        if not ltp:
            return MarketStatus(0, 0, 0, 0, False, False, "Price unavailable")

        open_price, estimated = self._get_open(connector, ltp)
        pct = ((ltp - open_price) / open_price) * 100

        logger.info(f"📈 Nifty: {ltp:.2f} | Open: {open_price:.2f}"
                    f"{' (ESTIMATED)' if estimated else ''} | Change: {pct:+.2f}%")

        if pct <= self.trigger3:    level = 3
        elif pct <= self.trigger2:  level = 2
        elif pct <= self.trigger1:  level = 1
        else:                       level = 0

        acc        = self._check_acc()
        should_buy = level > 0 or (acc and pct < -1.0)

        # If we're relying on an estimated open price, don't let it trigger a
        # real buy — only real market data should authorize spending money.
        if estimated and should_buy:
            logger.warning("⚠️ Trigger hit but open price is ESTIMATED (candle data "
                            "unavailable) — suppressing buy signal this cycle for safety")
            should_buy = False

        if level == 3:   reason = f"🚨 MAJOR FALL {pct:.2f}% — TRANCHE 3!"
        elif level == 2: reason = f"🔴 BIG FALL {pct:.2f}% — TRANCHE 2!"
        elif level == 1: reason = f"🟠 FALL {pct:.2f}% — TRANCHE 1!"
        elif acc:        reason = f"📉 ACCUMULATION — down {abs(pct):.2f}% today"
        else:            reason = f"✅ Normal {pct:+.2f}% — waiting..."

        logger.info(reason)
        return MarketStatus(ltp, open_price, round(pct, 4), level, acc, should_buy, reason, estimated)

    def update_history(self, pct):
        self._history.append(pct)
        if len(self._history) > 10:
            self._history.pop(0)

    def _check_acc(self):
        if len(self._history) < self.acc_days:
            return False
        return all(x < 0 for x in self._history[-self.acc_days:])

    def _get_open(self, connector, ltp):
        """Returns (open_price, is_estimated). Tries real candle data first;
        falls back to a rough estimate only when the API call fails, and
        clearly flags that fallback so callers can treat it with caution
        (see check() above, which suppresses buy signals on estimated data)."""
        try:
            data = connector.smart.getCandleData({
                "exchange": NIFTY_EXCHANGE, "symboltoken": NIFTY_TOKEN,
                "interval": "ONE_DAY",
                "fromdate": datetime.now().strftime("%Y-%m-%d 09:00"),
                "todate":   datetime.now().strftime("%Y-%m-%d 16:00"),
            })
            if data["status"] and data["data"]:
                return float(data["data"][-1][1]), False
        except Exception as e:
            logger.warning(f"⚠️ Candle data unavailable, estimating open price: {e}")
        return ltp * 1.005, True
