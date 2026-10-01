"""
ETF Trading Bot — buys pre-configured ETFs on market dips via Angel One SmartAPI.

HOW TO RUN:
  python bot.py --test                        # Test connection only
  python bot.py --dry-run                     # Simulate, no real orders
  python bot.py                               # LIVE trading
  python bot.py --test-telegram               # Send one sample of each Telegram message
  python bot.py --simulate-trigger CPSEETF -3.0  # Inject fake trigger (dry-run only)

The bot exits automatically at 15:35 IST after sending a daily summary.
Run it every weekday at 9:10 AM via Windows Task Scheduler.
See README.md for full setup instructions.
"""

import sys
import time
import traceback
from datetime import datetime, time as dtime

import schedule
from loguru import logger
from zoneinfo import ZoneInfo

from version import __version__
from ip_guard import check_ip

from config import load_config, ConfigError
from connector import AngelOneConnector
from nav_checker import ETF_LIST, NAVChecker
from tranche_engine import AllocationEngine, compute_allocations
from notifier import Notifier, E, Templates, StatusCommandPoller, send_test_messages
from market_detector import MarketFallDetector

logger.add("logs/bot_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days")

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN      = dtime(9, 15)
MARKET_CLOSE     = dtime(15, 30)
BUY_WINDOW_START = dtime(15, 0)
BUY_WINDOW_END   = dtime(15, 15)
EOD_EXIT_TIME    = dtime(15, 35)
HEARTBEAT_TIME   = dtime(9, 20)
ALERT_THRESHOLD  = 5.0              # alert if ETF moves > 5% intraday
FEED_FAIL_LIMIT  = 3                # cycles before "price feed failing" alert
BUDGET_LOW_PCT   = 0.20             # alert when < 20% monthly budget remains


def _now_ist() -> datetime:
    return datetime.now(IST)


def _is_trading_day(config) -> bool:
    now      = _now_ist()
    date_str = now.strftime("%Y-%m-%d")
    if now.weekday() >= 5:
        logger.info(f"📅 {now.strftime('%A')} — market closed (weekend)")
        return False
    if date_str in config.nse_holidays:
        logger.info(f"📅 {date_str} is an NSE holiday — market closed")
        return False
    return True


class TradingBot:
    """
    MODE determines what happens in the buy window:

      alert_only  — compute signals and send Telegram BUY SIGNAL; NEVER call
                    place_buy_order or any order API on either broker.
      dry_run     — simulate the full order flow with fake order IDs and
                    separate state files; no real orders placed.
      live        — real orders via Angel One; requires explicit confirmation
                    at startup (--confirm-live or typed YES).

    The effective mode is shown in every Telegram message, the console banner,
    and the window title set by the launcher.
    """

    def __init__(self, config, mode: str | None = None,
                 simulated_triggers: dict | None = None):
        # Resolve effective mode: CLI flag > config.mode
        self.mode = (mode or config.mode).lower()
        if self.mode not in ("alert_only", "dry_run", "live"):
            raise ValueError(f"Unknown mode: {self.mode!r}")

        self.config             = config
        self.simulated_triggers = simulated_triggers or {}

        # alert_only and dry_run both use no-real-order path in engine
        is_dry = self.mode in ("alert_only", "dry_run")

        self.connector  = AngelOneConnector(config)
        self.engine     = AllocationEngine(
            config   = config,
            dry_run  = is_dry,
            mode     = self.mode,
        )
        self.notifier   = Notifier(
            token      = config.telegram_token,
            chat_id    = config.telegram_chat_id,
            dry_run    = is_dry,
            chat_id_2  = getattr(config, "telegram_chat_id_2", ""),
            mode       = self.mode,
        )
        self.market_det = MarketFallDetector()
        self.cmd_poller = StatusCommandPoller(
            token           = config.telegram_token,
            allowed_chat_id = config.telegram_chat_id,
        )

        self.kill               = False
        self._triggered_today:  dict[str, float] = {}
        self._last_notified_pct: dict[str, float] = {}
        self._heartbeat_sent    = False
        self._feed_fail_count   = 0
        self._quotes_cache:     dict[str, tuple] = {}
        self._nifty_pct: float | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self):
        _MODE_BANNERS = {
            "alert_only": "📊 ALERT ONLY — signals sent to Telegram, NO orders placed",
            "dry_run":    "🧪 DRY RUN   — simulated orders, separate state files",
            "live":       "💰 LIVE      — REAL ORDERS with REAL MONEY",
        }
        banner = _MODE_BANNERS[self.mode]
        logger.info(f"🚀 Bot starting  [{self.mode.upper()}]")
        logger.info(f"   {banner}")

        if self.mode == "live":
            # Refuse to run live without explicit confirmation already received
            # (main() handles the interactive prompt before calling start())
            logger.warning("⚠️  LIVE mode confirmed — real orders will be placed")

        if not _is_trading_day(self.config):
            logger.info("📴 Not a trading day — exiting")
            return

        if not self.connector.login():
            logger.error("❌ Login failed — bot cannot start")
            self.notifier.notify_error(E.LOGIN_FAILED, "generateSession failed — check credentials")
            return

        logger.success("✅ Connected to Angel One")

        # ── IP check for live mode ────────────────────────────────────────
        if self.mode == "live":
            ip_result = check_ip(self.config.registered_static_ip)
            if not ip_result.match:
                logger.warning(f"⚠️ IP check: {ip_result.reason}")
                self.notifier.notify_error(
                    "IP_MISMATCH",
                    f"IP check failed — falling back to alert_only.\n{ip_result.reason}"
                )
                logger.warning("⚠️ Switching to alert_only for this session")
                self.mode = "alert_only"
                self.engine._mode = "alert_only"
        self._send_startup_notification()

        schedule.every(5).minutes.do(self._safe_run)
        self._safe_run()  # immediate first cycle

        try:
            while not self.kill:
                schedule.run_pending()
                self.cmd_poller.poll(
                    self.notifier,
                    watchlist_fn  = self._current_watchlist,
                    budget_fn     = lambda: (
                        self.engine.monthly_state.budget_used,
                        self.engine.monthly_state.budget_total,
                    ),
                )
                time.sleep(3)
        except KeyboardInterrupt:
            logger.info("⛔ Bot stopped (Ctrl+C)")
            self.notifier.bot_stopped("manual stop (Ctrl+C)")
        except Exception as e:
            logger.exception("💥 Unhandled crash in main loop")
            tb = traceback.format_exc()[-500:]
            self.notifier.notify_error(
                E.BOT_CRASHED,
                f"{type(e).__name__}: {e}\n---\n{tb}"
            )
            raise
        finally:
            self._send_daily_summary()
            self.connector.logout()
            self.notifier.stop()

    def _safe_run(self):
        try:
            self._run()
        except Exception as e:
            logger.exception(f"💥 Error during scheduled run: {e}")
            self.notifier.notify_error(
                E.BOT_CRASHED,
                f"Cycle error: {type(e).__name__}: {e}"
            )

    # ── Core cycle ───────────────────────────────────────────────────────

    def _run(self):
        if self.kill:
            return

        now_dt = _now_ist()
        now    = now_dt.time()

        # EOD auto-exit
        if now >= EOD_EXIT_TIME:
            logger.info("🏁 EOD — bot exiting after market close")
            self.kill = True
            return

        if not (MARKET_OPEN <= now <= MARKET_CLOSE):
            logger.info(f"⏰ Market closed ({now.strftime('%H:%M')} IST)")
            return

        logger.info(f"\n{'='*45}\n⏰ {now_dt.strftime('%H:%M:%S')} IST")

        # ── Heartbeat at 9:20 AM ──────────────────────────────────────────
        if not self._heartbeat_sent and now >= HEARTBEAT_TIME:
            self.notifier.heartbeat(len(ETF_LIST))
            self._heartbeat_sent = True

        # ── Nifty market-wide fall alert ──────────────────────────────────
        try:
            mkt = self.market_det.check(self.connector)
            self._nifty_pct = mkt.change_pct
            if mkt.change_pct <= -2.0:
                self.notifier.nifty_fall(mkt.change_pct, mkt.reason)
        except Exception as e:
            logger.warning(f"⚠️ Market detector error (non-fatal): {e}")

        # ── Funds check ───────────────────────────────────────────────────
        funds = None
        if self.mode == "live":
            # Stop if Angel already rejected for IP today
            if getattr(self.connector, "_ip_rejected_today", False):
                logger.warning("⚠️ Switching to alert_only — IP rejection detected")
                self.mode = "alert_only"
                self.engine._mode = "alert_only"
                self.notifier.notify_error("IP_MISMATCH",
                    "Switched to alert_only — Angel One rejected an order for IP/compliance.")
            else:
                funds = self.connector.get_funds()
                if funds is None:
                    self._feed_fail_count += 1
                    if self._feed_fail_count >= FEED_FAIL_LIMIT:
                        self.notifier.notify_error(
                            E.PRICE_FEED_FAILING,
                            f"get_funds() failed {self._feed_fail_count} consecutive cycles"
                        )
                    logger.warning("⚠️ Cannot read available funds — will re-check next cycle")
                return
            logger.info(f"💵 Available funds: ₹{funds:,.2f}")
            self._feed_fail_count = 0

        # ── ETF price scan ────────────────────────────────────────────────
        triggered: dict[str, float] = {}
        failed_quotes = 0

        for sym, info in ETF_LIST.items():
            # Inject simulated triggers if requested
            if sym in self.simulated_triggers:
                fake_pct  = self.simulated_triggers[sym]
                fake_open = 100.0
                fake_ltp  = fake_open * (1 + fake_pct / 100)
                ltp, day_open = fake_ltp, fake_open
                logger.info(f"🧪 SIMULATED {sym}: ltp={ltp:.2f} open={day_open:.2f} pct={fake_pct:+.2f}%")
            else:
                ltp, day_open = self.connector.get_quote(
                    info["exchange"], sym, info["token"]
                )

            if ltp is None or day_open is None:
                logger.warning(f"⚠️ No quote for {sym} this cycle — skipping")
                failed_quotes += 1
                continue

            change_pct = ((ltp - day_open) / day_open) * 100
            trigger    = info["trigger_pct"]
            self._quotes_cache[sym] = (ltp, day_open)

            logger.info(
                f"  {sym}: ₹{ltp:.2f} | open ₹{day_open:.2f} "
                f"({change_pct:+.2f}%) | trigger {trigger}%"
            )

            # ── Trigger detection + Telegram alerts ───────────────────────
            if change_pct <= trigger:
                triggered[sym] = change_pct

                if sym not in self._triggered_today:
                    # First time hitting trigger today
                    self._triggered_today[sym] = change_pct
                    self._last_notified_pct[sym] = change_pct
                    self.notifier.trigger_hit(
                        sym, change_pct, trigger, day_open, ltp
                    )
                else:
                    # Already triggered — check for escalation (further 1pp drop)
                    last_notif = self._last_notified_pct.get(sym, change_pct)
                    if change_pct <= last_notif - 1.0:
                        self._last_notified_pct[sym] = change_pct
                        self.notifier.trigger_hit(
                            sym, change_pct, trigger, day_open, ltp,
                            escalation=True
                        )
            else:
                # Recovered above trigger?
                if sym in self._triggered_today and sym not in self.engine.daily_state.bought_symbols:
                    self.notifier.trigger_recovered(sym, change_pct, ltp)
                    del self._triggered_today[sym]
                    self._last_notified_pct.pop(sym, None)

            # Big move alert (independent of trigger)
            if abs(change_pct) >= ALERT_THRESHOLD:
                self.notifier.notify(
                    E.NIFTY_FALL if sym == "NIFTY" else E.TRIGGER_HIT,
                    Templates.trigger_hit(
                        (self.mode in ("alert_only", "dry_run")), sym, change_pct, trigger, day_open, ltp
                    ),
                    dedupe_key=f"bigmove::{sym}",
                )

        # Feed failure alert
        if failed_quotes >= len(ETF_LIST):
            self._feed_fail_count += 1
            if self._feed_fail_count >= FEED_FAIL_LIMIT:
                self.notifier.notify_error(
                    E.PRICE_FEED_FAILING,
                    f"All ETF quotes failed for {self._feed_fail_count} consecutive cycles"
                )
        else:
            self._feed_fail_count = 0

        if not triggered:
            logger.info("😴 No ETF hit trigger — watching...")
            return

        logger.info(f"🎯 Triggered: {triggered}")

        in_buy_window = BUY_WINDOW_START <= now <= BUY_WINDOW_END
        if not in_buy_window:
            logger.info("👁️ Triggers detected — waiting for buy window (3:00–3:15 PM)")
            return

        # ── Buy window ────────────────────────────────────────────────────
        logger.info("🕒 BUY WINDOW — executing orders!")

        # Send buy window plan notification
        daily_cap = min(
            self.engine.monthly_state.remaining,
            self.config.daily_cap_pct * self.config.monthly_budget,
        )
        allocations = compute_allocations(
            triggered,
            self.engine.monthly_state.remaining,
            daily_cap,
            self.engine.daily_state.bought_symbols,
        )
        plan = [
            {"symbol": sym, "change_pct": triggered[sym], "allocation": alloc}
            for sym, alloc in allocations.items()
        ]
        self.notifier.buy_window_open(plan)

        if self.mode == "alert_only":
            # ALERT_ONLY: compute and send BUY SIGNAL — ZERO order API calls
            logger.info("📊 ALERT ONLY — sending BUY SIGNAL, no orders placed")
            self._send_buy_signals(triggered, allocations)
            return

        orders, skips = self.engine.execute(
            triggered, self.connector, funds_available=funds
        )

        # ── Skip notifications ────────────────────────────────────────────
        for skip in skips:
            self.notifier.buy_skipped(skip["symbol"], skip["reason"])

        # ── Order notifications ───────────────────────────────────────────
        for o in orders:
            fill = o.get("fill_status", "placed")
            if fill in ("complete", "filled", "dry_run"):
                self.notifier.order_filled(
                    symbol          = o["symbol"],
                    qty             = o["quantity"],
                    avg_price       = o["price"],
                    amount          = o["amount"],
                    order_id        = o["order_id"],
                    nav_premium     = o.get("premium", 0.0),
                    budget_used     = self.engine.monthly_state.budget_used,
                    budget_remaining= self.engine.monthly_state.remaining,
                )
            elif fill in ("rejected",):
                self.notifier.order_rejected(o["symbol"], o["order_id"], fill)
            elif fill in ("timeout", "cancelled"):
                self.notifier.order_unfilled(o["symbol"], o["order_id"], fill)

        # Window closed with triggers but nothing bought
        if not orders and triggered:
            self.notifier.window_closed_nothing(list(triggered.keys()))

        # ── Budget alerts ─────────────────────────────────────────────────
        ms = self.engine.monthly_state
        if ms.is_exhausted:
            self.notifier.budget_exhausted(ms.budget_total)
        elif ms.budget_total > 0:
            pct_used = ms.budget_used / ms.budget_total
            if pct_used >= (1.0 - BUDGET_LOW_PCT):
                self.notifier.budget_low(ms.budget_used, ms.budget_total)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _send_buy_signals(self, triggered: dict[str, float],
                          allocations: dict[str, float]) -> None:
        """Send BUY SIGNAL Telegram messages in alert_only mode.
        Makes ZERO order API calls."""
        from nav_checker import NAVChecker
        nav_checker = NAVChecker()
        for sym, alloc in allocations.items():
            info = ETF_LIST.get(sym, {})
            ltp, day_open = self._quotes_cache.get(sym, (None, None))
            change_pct    = triggered.get(sym, 0.0)

            if not ltp:
                logger.warning(f"⚠️ {sym}: no LTP for buy signal — skipping")
                continue

            qty = int(alloc / ltp) if ltp else 0
            limit_price = round(ltp * 1.001, 2) if qty > 0 else None

            # NAV info (advisory — never blocks the signal)
            nav_result = nav_checker.check(sym, ltp,
                                           mode=self.config.nav_check_mode)
            nav_note = nav_result.reason

            msg = (
                f"📊 <b>BUY SIGNAL</b>  {self.notifier._mode_tag()}\n"
                f"<b>{self._h(sym)}</b>\n"
                f"Fall:        {change_pct:+.2f}%  (trigger {info.get('trigger_pct',0):+.1f}%)\n"
                f"Day open:    Rs.{day_open:.2f}\n"
                f"Current LTP: Rs.{ltp:.2f}\n"
                f"Suggested:   {qty} units @ Rs.{limit_price} (LIMIT, DAY)\n"
                f"Amount:      Rs.{qty * ltp:.2f}\n"
                f"NAV:         {self._h(nav_note)}\n"
                f"Budget left: Rs.{self.engine.monthly_state.remaining:.0f}\n"
                f"Today cap:   Rs.{min(self.engine.monthly_state.remaining, self.config.daily_cap_pct * self.config.monthly_budget):.0f}\n"
                f"\n<i>This is an alert. No order was placed.</i>"
            )
            self.notifier.notify(E.ORDER_PLACED, msg[:4000])
            logger.info(f"📊 BUY SIGNAL sent for {sym}: qty={qty} @ Rs.{limit_price}")

    @staticmethod
    def _h(text) -> str:
        import html
        return html.escape(str(text))

    def _send_startup_notification(self):
        watchlist = []
        for sym, info in ETF_LIST.items():
            ltp, day_open = self.connector.get_quote(
                info["exchange"], sym, info["token"]
            )
            self._quotes_cache[sym] = (ltp, day_open)
            pct = ((ltp - day_open) / day_open * 100) if (ltp and day_open) else None
            watchlist.append({
                "symbol":     sym,
                "ltp":        ltp,
                "day_open":   day_open,
                "change_pct": pct,
                "trigger_pct": info["trigger_pct"],
                "triggered":  (pct is not None and pct <= info["trigger_pct"]),
            })

        daily_cap = min(
            self.engine.monthly_state.remaining,
            self.config.daily_cap_pct * self.config.monthly_budget,
        )
        self.notifier.bot_started(
            watchlist    = watchlist,
            budget_used  = self.engine.monthly_state.budget_used,
            budget_total = self.engine.monthly_state.budget_total,
            daily_cap    = daily_cap,
        )

    def _send_daily_summary(self):
        try:
            etf_changes = []
            for sym, info in ETF_LIST.items():
                cached = self._quotes_cache.get(sym, (None, None))
                ltp, day_open = cached
                pct = ((ltp - day_open) / day_open * 100) if (ltp and day_open) else None
                etf_changes.append({
                    "symbol":    sym,
                    "change_pct": pct,
                    "triggered": sym in self._triggered_today,
                })

            self.notifier.daily_summary(
                etf_changes     = etf_changes,
                nifty_pct       = self._nifty_pct,
                triggered_syms  = list(self._triggered_today.keys()),
                orders          = self.engine.daily_state.orders,
                budget_used     = self.engine.monthly_state.budget_used,
                budget_total    = self.engine.monthly_state.budget_total,
            )
        except Exception as e:
            logger.warning(f"⚠️ Could not send daily summary: {e}")

    def _current_watchlist(self) -> list[dict]:
        """Return current cached watchlist for /status command."""
        result = []
        for sym, info in ETF_LIST.items():
            cached = self._quotes_cache.get(sym, (None, None))
            ltp, day_open = cached
            pct = ((ltp - day_open) / day_open * 100) if (ltp and day_open) else None
            result.append({
                "symbol":    sym,
                "ltp":       ltp,
                "change_pct": pct,
                "triggered": sym in self._triggered_today,
            })
        return result


# ── Entry point ───────────────────────────────────────────────────────────────

def _confirm_live_mode() -> bool:
    """Require explicit interactive confirmation before starting live mode.
    Returns True if confirmed, False if aborted."""
    print("\n" + "=" * 60)
    print("  ⚠️  LIVE MODE — REAL ORDERS WITH REAL MONEY  ⚠️")
    print("=" * 60)
    print("  MODE=live is set.")
    print("  The bot WILL place real buy orders during 3:00-3:15 PM IST.")
    print("  Use MODE=alert_only to get signals without placing orders.")
    print("=" * 60)
    try:
        answer = input("\n  Type YES to continue, anything else to abort: ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer == "YES":
        return True
    print("  Aborted. Change MODE=alert_only in .env for safe operation.")
    return False


def _parse_simulate_trigger(argv: list[str]) -> dict[str, float]:
    """Parse --simulate-trigger SYMBOL PCT from argv."""
    triggers = {}
    i = 0
    while i < len(argv):
        if argv[i] == "--simulate-trigger" and i + 2 < len(argv):
            sym = argv[i + 1].upper()
            try:
                pct = float(argv[i + 2])
                triggers[sym] = pct
            except ValueError:
                print(f"⚠️ Invalid pct for --simulate-trigger: {argv[i+2]}", file=sys.stderr)
            i += 3
        else:
            i += 1
    return triggers


def main():
    try:
        config = load_config()
    except ConfigError:
        sys.exit(1)

    # ── Resolve effective mode: CLI flags override .env ───────────────────
    if "--alert-only" in sys.argv:
        effective_mode = "alert_only"
    elif "--dry-run" in sys.argv:
        effective_mode = "dry_run"
    elif "--live" in sys.argv:
        effective_mode = "live"
    else:
        effective_mode = config.mode   # from .env (default: alert_only)

    # ── --test: connection test only ──────────────────────────────────────
    if "--test" in sys.argv:
        c = AngelOneConnector(config)
        if c.login():
            funds = c.get_funds()
            if funds is not None:
                logger.success(f"✅ Connected! Funds: Rs.{funds:,.2f}")
            else:
                logger.warning("✅ Connected but could not read funds")
            c.logout()
        else:
            logger.error("❌ Connection test failed")
            sys.exit(1)
        return

    # ── --test-telegram: send sample of every message type ───────────────
    if "--test-telegram" in sys.argv:
        notifier = Notifier(
            token     = config.telegram_token,
            chat_id   = config.telegram_chat_id,
            dry_run   = True,
            chat_id_2 = getattr(config, "telegram_chat_id_2", ""),
        )
        if not notifier.enabled:
            logger.error("❌ Telegram not configured — set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in .env")
            sys.exit(1)
        logger.info("🧪 Sending test messages for every event type…")
        send_test_messages(notifier)
        time.sleep(3)
        notifier.stop()
        logger.success("✅ Test messages sent — check Telegram")
        return

    # ── Live mode: require explicit confirmation ───────────────────────────
    if effective_mode == "live":
        if "--confirm-live" not in sys.argv:
            if not _confirm_live_mode():
                sys.exit(0)
        else:
            logger.warning("⚠️  --confirm-live flag present — skipping interactive prompt")

    # ── --simulate-trigger: inject fake trigger ───────────────────────────
    simulated = _parse_simulate_trigger(sys.argv)
    if simulated:
        if effective_mode == "live":
            logger.error("❌ --simulate-trigger cannot be used in live mode")
            sys.exit(1)
        logger.info(f"🧪 Simulated triggers: {simulated}")

    TradingBot(config, mode=effective_mode,
               simulated_triggers=simulated).start()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("💥 Fatal error — bot exiting")
        traceback.print_exc()
        sys.exit(1)
