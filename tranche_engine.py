"""
Smart allocation engine for the ETF dip-buying bot.

Budget rules
────────────
1. Silver ETF (ICICISILVER):
     Trigger ≤ -6.5 % → fixed ₹350 allocation (SILVER_FIXED).
2. Other ETFs:
     Trigger met → remaining budget divided proportionally to fall magnitude.
     Bigger fall = bigger share.
3. Daily cap:
     On any single day the engine spends at most
       min(monthly_remaining, DAILY_CAP_PCT × monthly_budget).
     This prevents a single large dip day from consuming the entire month.
4. Per-symbol guard:
     Each symbol can be bought at most once per calendar day.
5. Monthly rollover:
     When the calendar month changes, budget_used resets to 0.
6. Quantity rounding:
     int(allocation / ltp) is used.  Any unspent amount (due to rounding)
     is NOT counted as spent — it is carried back to the remaining pool.

State files
───────────
Live mode : tranche_state.json      / monthly_budget.json
Dry mode  : tranche_state.dry.json  / monthly_budget.dry.json

Corrupt state files are backed up as *.corrupt and an alert is sent.
In live mode, buying is blocked until the corrupt file is removed.

Order fill verification (live only)
────────────────────────────────────
After placing a live order the engine polls orderBook for up to
ORDER_POLL_SECS seconds.  Only a "complete" status counts as spent.
A rejected or timed-out order is NOT counted, and bought_today / per-symbol
flags are NOT set — the next cycle can retry.  A LIMIT order still open
at the end of the buy window is cancelled and alerted.
"""

import os
import json
import shutil
import tempfile
import time
from datetime import date, datetime
from dataclasses import dataclass, field
from loguru import logger

from nav_checker import ETF_LIST, NAVChecker

# ── File paths ────────────────────────────────────────────────────────────────

def _state_files(dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "tranche_state.dry.json", "monthly_budget.dry.json"
    return "tranche_state.json", "monthly_budget.json"


SILVER_SYMBOL = "SILVERIETF-EQ"  # verified symbol in Angel master
SILVER_FIXED  = 350.0

ORDER_POLL_SECS  = 60    # max seconds to wait for an order to fill
ORDER_POLL_EVERY = 5     # poll interval in seconds


# ── Atomic JSON I/O ───────────────────────────────────────────────────────────

def _atomic_write_json(path: str, data: dict) -> None:
    """Write JSON atomically: temp file → fsync → rename.
    A crash mid-write always leaves a complete old or new file, never a
    half-written one."""
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_name, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _safe_load_json(path: str, required_keys: set, alerts=None) -> dict | None:
    """Load JSON and validate required keys.

    On corruption: backs the file up as <path>.corrupt, logs ERROR, sends
    alert, returns None.  Caller must decide whether to abort.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        missing = required_keys - set(data.keys())
        if missing:
            raise ValueError(f"missing keys: {missing}")
        return data
    except FileNotFoundError:
        return None                 # normal: file doesn't exist yet
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        corrupt = path + ".corrupt"
        try:
            shutil.copy2(path, corrupt)
        except Exception:
            pass
        msg = (f"❌ State file {path} is corrupt ({e}). "
               f"Backed up as {corrupt}. Remove it to resume.")
        logger.error(msg)
        if alerts:
            alerts.send(msg)
        return None                 # signals "corrupt" to caller


# ── State dataclasses ─────────────────────────────────────────────────────────

@dataclass
class TrancheState:
    date:            str
    bought_today:    bool              = False
    total_invested:  float             = 0.0
    bought_symbols:  list              = field(default_factory=list)  # per-symbol guard
    orders:          list              = field(default_factory=list)


@dataclass
class MonthlyState:
    month:        str
    budget_used:  float
    budget_total: float
    orders:       list  = field(default_factory=list)

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget_total - self.budget_used)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining <= 0.0


# ── Shared allocation logic (used by both engine and backtest) ────────────────

def compute_allocations(triggered_etfs: dict,
                        remaining: float,
                        daily_cap: float,
                        bought_symbols: list[str]) -> dict[str, float]:
    """
    Pure function — no side effects.  Returns {symbol: amount_to_spend}.

    Parameters
    ──────────
    triggered_etfs  : {symbol: change_pct}
    remaining       : monthly budget remaining (₹)
    daily_cap       : maximum spend today (₹) = min(remaining, cap_pct × monthly)
    bought_symbols  : symbols already bought today (excluded from allocations)

    The daily cap is applied across Silver + other together.
    """
    spendable = min(remaining, daily_cap)
    if spendable <= 0:
        return {}

    allocations: dict[str, float] = {}

    # ── Silver fixed allocation ───────────────────────────────────────────
    silver_spent = 0.0
    if SILVER_SYMBOL in triggered_etfs and SILVER_SYMBOL not in bought_symbols:
        if triggered_etfs[SILVER_SYMBOL] <= -6.5:
            silver_alloc = min(SILVER_FIXED, spendable)
            allocations[SILVER_SYMBOL] = silver_alloc
            silver_spent = silver_alloc

    # ── Proportional for other ETFs ───────────────────────────────────────
    other = {
        sym: pct
        for sym, pct in triggered_etfs.items()
        if sym != SILVER_SYMBOL
        and sym not in bought_symbols
        and sym in ETF_LIST
        and pct <= ETF_LIST[sym]["trigger_pct"]
    }

    other_budget = spendable - silver_spent
    if other and other_budget > 0:
        total_fall = sum(abs(p) for p in other.values())
        raw: dict[str, float] = {
            sym: round(other_budget * abs(pct) / total_fall, 2)
            for sym, pct in other.items()
        }
        # Rounding correction: first symbol absorbs the difference
        diff = other_budget - sum(raw.values())
        if raw:
            first = next(iter(raw))
            raw[first] = round(raw[first] + diff, 2)
        allocations.update(raw)

    return allocations


# ── Engine ────────────────────────────────────────────────────────────────────

class AllocationEngine:
    """Allocates the monthly budget across ETFs and manages state."""

    def __init__(self, config, dry_run: bool = False, alerts=None, mode: str = "alert_only"):
        self.config      = config
        self.dry_run     = dry_run
        self.alerts      = alerts
        self.nav_checker = NAVChecker()
        self._mode        = mode

        self._state_file, self._monthly_file = _state_files(dry_run)
        self._corrupt     = False          # True → block live buys

        self.daily_state   = self._load_daily()
        self.monthly_state = self._load_monthly()

    # ── Public API ────────────────────────────────────────────────────────

    def execute(self, triggered_etfs: dict, connector,
                funds_available: float | None = None) -> tuple[list, list]:
        """
        triggered_etfs : {symbol: change_pct}
        connector      : AngelOneConnector
        funds_available: available funds from get_funds() (None in dry mode)

        Returns (orders, skips) where:
          orders : list of order dicts for successfully filled orders
          skips  : list of {"symbol": str, "reason": str} for every skipped ETF
        """
        self._refresh_state()   # daily reset + month rollover
        skips: list[dict] = []

        # alert_only must NEVER call any order API
        if self._mode == "alert_only":
            logger.info("📊 alert_only mode — engine skipped, no orders placed")
            return [], []

        if self._corrupt and not self.dry_run:
            logger.error("❌ Corrupt state file detected — refusing live orders until fixed")
            for sym in triggered_etfs:
                skips.append({"symbol": sym, "reason": "state file corrupt — live orders blocked"})
            return [], skips

        if self.daily_state.bought_today:
            logger.info("⏭️ Already bought today — skipping")
            for sym in triggered_etfs:
                skips.append({"symbol": sym, "reason": "already bought today"})
            return [], skips

        if self.monthly_state.is_exhausted:
            logger.warning(f"🚫 Monthly budget ₹{self.config.monthly_budget:.0f} exhausted")
            for sym in triggered_etfs:
                skips.append({"symbol": sym, "reason": "monthly budget exhausted"})
            return [], skips

        # Daily cap = min(monthly_remaining, cap_pct × monthly_budget)
        daily_cap = min(
            self.monthly_state.remaining,
            self.config.daily_cap_pct * self.config.monthly_budget,
        )
        logger.info(
            f"💰 Monthly remaining: ₹{self.monthly_state.remaining:.2f} | "
            f"Daily cap: ₹{daily_cap:.2f}"
        )

        allocations = compute_allocations(
            triggered_etfs,
            self.monthly_state.remaining,
            daily_cap,
            self.daily_state.bought_symbols,
        )

        # Symbols excluded by daily cap or already-bought guard
        for sym in triggered_etfs:
            if sym in self.daily_state.bought_symbols:
                skips.append({"symbol": sym, "reason": "already bought today (per-symbol guard)"})
            elif sym not in allocations:
                skips.append({"symbol": sym, "reason": "excluded by daily cap or allocation logic"})

        if not allocations:
            logger.info("😴 Nothing to allocate this cycle")
            return [], skips

        logger.info(f"📊 Allocations: {allocations}")

        orders: list[dict] = []
        running_unspent = 0.0   # rounding carry-back pool

        for sym, alloc in allocations.items():
            effective_alloc = alloc + running_unspent
            try:
                order, skip_reason = self._buy_one_etf(
                    sym, effective_alloc, connector, funds_available
                )
            except Exception as e:
                logger.error(f"❌ Unexpected error buying {sym}: {e}")
                if self.alerts:
                    self.alerts.send(f"❌ Error buying {sym}: {e}")
                skips.append({"symbol": sym, "reason": f"unexpected error: {e}"})
                continue

            if order is None:
                # Nothing bought — carry unused alloc into next symbol
                running_unspent += effective_alloc
                if skip_reason:
                    skips.append({"symbol": sym, "reason": skip_reason})
                continue

            actual_spent    = order["amount"]
            running_unspent = round(effective_alloc - actual_spent, 2)
            if running_unspent > 0:
                logger.info(
                    f"↩️ Rounding carry-back ₹{running_unspent:.2f} "
                    f"(alloc ₹{effective_alloc:.2f} − spent ₹{actual_spent:.2f})"
                )

            # ── Per-order state save BEFORE attempting next ETF ────────────
            self.daily_state.bought_symbols.append(sym)
            self.daily_state.total_invested += actual_spent
            self.daily_state.orders.append(order)
            self.monthly_state.budget_used  += actual_spent
            self.monthly_state.orders.append(order)
            self._save_daily()
            self._save_monthly()

            # Daily loss limit guard
            if self.daily_state.total_invested >= self.config.daily_loss_limit:
                logger.warning(
                    f"🛑 Daily loss limit ₹{self.config.daily_loss_limit:.0f} reached "
                    f"— stopping buying for today"
                )
                if self.alerts:
                    self.alerts.send(
                        f"🛑 Daily loss limit ₹{self.config.daily_loss_limit:.0f} "
                        f"reached — no more buys today."
                    )
                orders.append(order)
                break

            orders.append(order)

        # Mark bought_today after at least one successful order
        if orders or self.daily_state.bought_symbols:
            self.daily_state.bought_today = True
            self._save_daily()

        if orders:
            total_spent = sum(o["amount"] for o in orders)
            logger.success(
                f"✅ Invested today: ₹{total_spent:.2f} | "
                f"Monthly used: ₹{self.monthly_state.budget_used:.2f} "
                f"/ ₹{self.config.monthly_budget:.0f}"
            )
            if self.monthly_state.is_exhausted:
                logger.warning("🚫 Monthly budget EXHAUSTED — no more buys this month!")

        return orders, skips

    # ── Internal buy ─────────────────────────────────────────────────────

    def _buy_one_etf(self, symbol: str, amount: float,
                     connector, funds_available: float | None
                     ) -> tuple[dict | None, str | None]:
        """Attempt to buy one ETF.
        Returns (order_dict, None) on success or (None, skip_reason) on skip."""
        info = ETF_LIST.get(symbol)
        if not info:
            logger.error(f"❌ {symbol} not in ETF_LIST — skipping")
            return None, f"{symbol} not in ETF_LIST"

        ltp = connector.get_ltp(info["exchange"], symbol, info["token"])
        if not ltp:
            logger.error(f"❌ Cannot get price for {symbol}")
            return None, "price feed unavailable"

        nav_result = self.nav_checker.check(
            symbol, ltp, mode=self.config.nav_check_mode
        )

        if nav_result.action == "SKIP":
            logger.warning(f"🚫 {symbol} SKIP — {nav_result.reason}")
            return None, f"NAV SKIP — {nav_result.reason}"

        if nav_result.action == "WAIT":
            logger.warning(f"⏳ {symbol} WAIT — {nav_result.reason}")
            return None, f"NAV WAIT — {nav_result.reason}"

        qty = int(amount / ltp)
        if qty <= 0:
            logger.warning(
                f"⚠️ {symbol} — ₹{amount:.0f} too small for 1 unit @ ₹{ltp:.2f}"
            )
            return None, f"quantity 0 — ₹{amount:.0f} too small for 1 unit @ ₹{ltp:.2f}"

        actual_cost = qty * ltp

        # Funds sufficiency check (live only)
        if not self.dry_run and funds_available is not None:
            if funds_available < actual_cost:
                msg = (
                    f"⚠️ {symbol}: insufficient funds "
                    f"(need ₹{actual_cost:.2f}, have ₹{funds_available:.2f}) — skipping"
                )
                logger.warning(msg)
                return None, f"insufficient funds (need ₹{actual_cost:.0f}, have ₹{funds_available:.0f})"

        # LIMIT orders price slightly ABOVE ltp so they actually fill
        use_limit = nav_result.use_limit
        price     = round(ltp * 1.001, 2) if use_limit else None

        logger.info(
            f"🛒 {symbol} x{qty} @ ₹{ltp:.2f} = ₹{actual_cost:.2f} "
            f"| {'LIMIT @' + str(price) if use_limit else 'MARKET'} "
            f"| {nav_result.reason}"
        )

        # ── Place order ──────────────────────────────────────────────────
        if self.dry_run:
            oid         = f"DRY_{symbol}_{int(ltp * 100)}"
            fill_status = "dry_run"
        else:
            oid = connector.place_buy_order(
                symbol, info["token"], qty, price, exchange=info["exchange"]
            )
            if not oid:
                return None, "order placement failed — broker rejected"

            fill_status = self._wait_for_fill(
                oid, connector, symbol, use_limit=use_limit
            )

            if fill_status not in ("complete", "filled"):
                msg = (
                    f"⚠️ {symbol} order {oid} not filled "
                    f"(status: {fill_status}) — NOT counting as spent"
                )
                logger.warning(msg)
                return None, f"order {fill_status} — not charged to budget"

        return {
            "symbol":      symbol,
            "quantity":    qty,
            "price":       ltp,
            "amount":      actual_cost,
            "order_type":  "LIMIT" if use_limit else "MARKET",
            "nav":         nav_result.nav,
            "premium":     nav_result.premium_pct,
            "order_id":    oid,
            "fill_status": fill_status,
            "date":        str(date.today()),
        }, None

    def _wait_for_fill(self, order_id: str, connector,
                       symbol: str, use_limit: bool) -> str:
        """Poll order status for up to ORDER_POLL_SECS.

        Returns the final status string.  If a LIMIT order is still open
        after the poll window it is cancelled.
        """
        from zoneinfo import ZoneInfo
        from datetime import time as dtime
        BUY_WINDOW_END = dtime(15, 15)
        IST = ZoneInfo("Asia/Kolkata")

        deadline = time.monotonic() + ORDER_POLL_SECS
        while time.monotonic() < deadline:
            status = connector.get_order_status(order_id)
            logger.info(f"  ↪ {symbol} order {order_id}: {status}")
            if status in ("complete", "filled"):
                return status
            if status in ("rejected", "cancelled"):
                return status
            time.sleep(ORDER_POLL_EVERY)

        # Timed out — cancel if LIMIT and still inside (or just past) buy window
        logger.warning(
            f"⚠️ {symbol} order {order_id} still open after {ORDER_POLL_SECS}s"
        )
        now_ist = datetime.now(IST).time()
        if use_limit and now_ist >= BUY_WINDOW_END:
            connector.cancel_order(order_id)
        return "timeout"

    # ── State management ─────────────────────────────────────────────────

    def _refresh_state(self) -> None:
        """Reset daily state on a new day; roll over monthly state on a new month."""
        today = str(date.today())
        month = today[:7]

        if self.daily_state.date != today:
            logger.info(f"📅 New trading day {today} — resetting daily state")
            self.daily_state = TrancheState(date=today)
            self._save_daily()

        # Month rollover — always sync budget_total from current config
        if self.monthly_state.month != month:
            logger.info(f"📅 New month {month} — resetting monthly budget")
            self.monthly_state = MonthlyState(
                month=month,
                budget_used=0.0,
                budget_total=self.config.monthly_budget,
            )
            self._save_monthly()
        elif self.monthly_state.budget_total != self.config.monthly_budget:
            # Config changed — update total but keep used
            logger.info(
                f"📝 Monthly budget changed: "
                f"₹{self.monthly_state.budget_total:.0f} → "
                f"₹{self.config.monthly_budget:.0f}"
            )
            self.monthly_state = MonthlyState(
                month=self.monthly_state.month,
                budget_used=self.monthly_state.budget_used,
                budget_total=self.config.monthly_budget,
                orders=self.monthly_state.orders,
            )
            self._save_monthly()

    def _load_daily(self) -> TrancheState:
        today    = str(date.today())
        required = {"date", "bought_today", "total_invested", "orders"}
        data     = _safe_load_json(self._state_file, required, self.alerts)

        if data is None and os.path.exists(self._state_file):
            # File exists but is corrupt
            self._corrupt = True
            return TrancheState(date=today)

        if data and data.get("date") == today:
            return TrancheState(
                date           =data["date"],
                bought_today   =data.get("bought_today", False),
                total_invested =data.get("total_invested", 0.0),
                bought_symbols =data.get("bought_symbols", []),
                orders         =data.get("orders", []),
            )
        return TrancheState(date=today)

    def _load_monthly(self) -> MonthlyState:
        month    = str(date.today())[:7]
        required = {"month", "budget_used", "budget_total"}
        data     = _safe_load_json(self._monthly_file, required, self.alerts)

        if data is None and os.path.exists(self._monthly_file):
            self._corrupt = True
            return MonthlyState(month=month, budget_used=0.0,
                                budget_total=self.config.monthly_budget)

        if data and data.get("month") == month:
            return MonthlyState(
                month        =data["month"],
                budget_used  =data.get("budget_used", 0.0),
                budget_total =self.config.monthly_budget,  # always from config
                orders       =data.get("orders", []),
            )
        return MonthlyState(month=month, budget_used=0.0,
                            budget_total=self.config.monthly_budget)

    def _save_daily(self) -> None:
        _atomic_write_json(self._state_file, {
            "date":           self.daily_state.date,
            "bought_today":   self.daily_state.bought_today,
            "total_invested": self.daily_state.total_invested,
            "bought_symbols": self.daily_state.bought_symbols,
            "orders":         self.daily_state.orders,
        })

    def _save_monthly(self) -> None:
        _atomic_write_json(self._monthly_file, {
            "month":        self.monthly_state.month,
            "budget_used":  self.monthly_state.budget_used,
            "budget_total": self.monthly_state.budget_total,
            "orders":       self.monthly_state.orders,
        })
