"""
ETF Trading Bot — buys pre-configured ETFs on market dips via Angel One SmartAPI.

HOW TO RUN:
  python bot.py --test      # Test connection only
  python bot.py --dry-run   # Simulate, no real orders
  python bot.py             # LIVE trading — places real orders

See README.md for full setup instructions and safety notes.
"""

import sys
import time
import traceback
from datetime import datetime, time as dtime

import schedule
from loguru import logger

from config import load_config, ConfigError
from connector import AngelOneConnector
from nav_checker import ETF_LIST, NAVChecker
from tranche_engine import AllocationEngine
from alerts import AlertManager

logger.add("logs/bot_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days")

MARKET_OPEN      = dtime(9, 15)
MARKET_CLOSE     = dtime(15, 30)
BUY_WINDOW_START = dtime(15, 0)
BUY_WINDOW_END   = dtime(15, 15)
ALERT_THRESHOLD  = 5.0   # Alert if any ETF moves > 5% in a day


class TradingBot:
    def __init__(self, config, dry_run=False):
        self.config    = config
        self.dry_run   = dry_run
        self.connector = AngelOneConnector(config)
        self.nav       = NAVChecker()
        self.engine    = AllocationEngine(monthly_budget=config.monthly_budget)
        self.alerts    = AlertManager(config.telegram_token, config.telegram_chat_id)
        self.kill      = False
        self._tracked  = {}   # symbol -> open price (for % tracking)

    def start(self):
        logger.info("🚀 Bot starting")
        if self.dry_run:
            logger.warning("🧪 DRY RUN — no real orders")

        if not self.connector.login():
            logger.error("❌ Login failed — bot cannot start")
            self.alerts.send("❌ Bot failed to start — login failed. Check credentials.")
            return

        logger.success("✅ Connected to Angel One")
        self.alerts.bot_started(self.dry_run)
        self._initialize_tracking()

        schedule.every(5).minutes.do(self._safe_run)
        self._safe_run()  # Run immediately once

        try:
            while True:
                schedule.run_pending()
                time.sleep(30)
        except KeyboardInterrupt:
            logger.info("⛔ Bot stopped (Ctrl+C)")
            self.alerts.bot_stopped("manual stop (Ctrl+C)")
        except Exception as e:
            # Anything unexpected that escapes _safe_run's own handling still
            # gets caught here so the bot never dies without telling you.
            logger.exception("💥 Unhandled crash in main loop")
            self.alerts.bot_crashed(f"{type(e).__name__}: {e}")
            raise
        finally:
            self.connector.logout()

    def _safe_run(self):
        """Wraps _run() so that a single bad cycle (e.g. one API call throwing
        an unexpected exception) logs, alerts, and moves on — instead of
        killing the whole scheduler and silently stopping all future runs.
        The original had no top-level exception handling around the
        scheduled job at all."""
        try:
            self._run()
        except Exception as e:
            logger.exception(f"💥 Error during scheduled run: {e}")
            self.alerts.send(f"⚠️ Bot hit an error this cycle (will retry next cycle): {e}")

    def _initialize_tracking(self):
        """Record opening prices of all ETFs for % tracking."""
        for sym, info in ETF_LIST.items():
            ltp = self.connector.get_ltp(info["exchange"], sym, info["token"])
            if ltp:
                open_price = self._get_etf_open(sym, ltp)
                self._tracked[sym] = open_price
                logger.info(f"📌 Tracking {sym} | Open: ₹{open_price:.2f} | LTP: ₹{ltp:.2f}")
            else:
                logger.warning(f"⚠️ Could not get price for {sym} at startup — will retry each cycle")

    def _get_etf_open(self, sym, ltp):
        """Get today's open price — first price recorded at market open."""
        import json
        from datetime import datetime as dt

        today = dt.now().strftime("%Y-%m-%d")
        try:
            with open("open_prices.json") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        if data.get("date") == today and sym in data:
            return float(data[sym])

        if data.get("date") != today:
            data = {"date": today}

        data[sym] = ltp
        from tranche_engine import _atomic_write_json
        _atomic_write_json("open_prices.json", data)

        logger.info(f"📌 {sym} open price recorded: ₹{ltp:.2f}")
        return ltp

    def _run(self):
        if self.kill:
            return

        now = datetime.now().time()
        if not (MARKET_OPEN <= now <= MARKET_CLOSE):
            logger.info(f"⏰ Market closed ({now.strftime('%H:%M')})")
            return

        logger.info(f"\n{'='*45}\n⏰ {datetime.now().strftime('%H:%M:%S')}")

        triggered = {}
        for sym, info in ETF_LIST.items():
            ltp = self.connector.get_ltp(info["exchange"], sym, info["token"])
            if not ltp:
                logger.warning(f"⚠️ No price for {sym} this cycle — skipping")
                continue

            open_price = self._tracked.get(sym, ltp)
            change_pct = ((ltp - open_price) / open_price) * 100
            trigger    = info["trigger_pct"]

            logger.info(f"  {sym}: ₹{ltp:.2f} ({change_pct:+.2f}%) | Trigger: {trigger}%")

            if change_pct <= trigger:
                triggered[sym] = change_pct

            if abs(change_pct) >= ALERT_THRESHOLD:
                self.alerts.send(f"🚨 {sym} BIG MOVE: {change_pct:+.2f}% today!\nPrice: ₹{ltp:.2f}")

        if not triggered:
            logger.info("😴 No ETF hit trigger — watching...")
            return

        logger.info(f"🎯 Triggered ETFs: {triggered}")

        in_buy_window = BUY_WINDOW_START <= now <= BUY_WINDOW_END
        if not in_buy_window:
            logger.info("👁️ Triggers detected but buy window is 3:00-3:15 PM only")
            return

        logger.info("🕒 BUY WINDOW — executing orders!")
        orders = self.engine.execute(triggered, self.connector, self.dry_run)

        if orders:
            report = self._build_report(orders)
            self.alerts.send(report)
            logger.success(report)

        if self.engine.monthly_state.is_exhausted:
            self.alerts.send(
                f"🚫 Monthly budget ₹{self.config.monthly_budget:.0f} exhausted!\n"
                f"No more buys this month."
            )

    def _build_report(self, orders):
        lines = ["📊 ETF BUY REPORT", "="*30]
        for o in orders:
            lines.append(
                f"✅ {o['symbol']}: {o['quantity']} units @ ₹{o['price']:.2f}"
                f"\n   Amount: ₹{o['amount']:.2f} | NAV premium: {o['premium']:+.2f}%"
            )
        lines.append(f"\n💰 Total today: ₹{sum(o['amount'] for o in orders):.2f}")
        lines.append(f"📅 Monthly used: ₹{self.engine.monthly_state.budget_used:.2f} / ₹{self.config.monthly_budget:.0f}")
        lines.append(f"💵 Remaining: ₹{self.engine.monthly_state.remaining:.2f}")
        return "\n".join(lines)


def main():
    try:
        config = load_config()
    except ConfigError:
        sys.exit(1)

    if "--test" in sys.argv:
        c = AngelOneConnector(config)
        if c.login():
            funds = c.get_funds()
            logger.success(f"✅ Connected! Funds: ₹{funds:,.2f}")
            c.logout()
        else:
            logger.error("❌ Failed!")
            sys.exit(1)
        return

    TradingBot(config, dry_run="--dry-run" in sys.argv).start()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Last-resort catch so a crash is at least visible in the log with a
        # full traceback, even if it happened before AlertManager existed.
        logger.exception("💥 Fatal error — bot exiting")
        traceback.print_exc()
        sys.exit(1)
