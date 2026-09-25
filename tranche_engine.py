"""Smart allocation engine.

Budget: monthly budget (default ₹1500) — configurable via MONTHLY_BUDGET in .env.

Rules:
1. Silver ETF (SILVERIETF):
   - Trigger: -6.5% or more
   - Fixed amount (no more, no less)

2. Other ETFs:
   - Trigger: their individual trigger %
   - Remaining budget divided PROPORTIONALLY
   - More fall % = more allocation
   - e.g. -5% gets more than -3%

3. Budget tracking:
   - Once monthly budget used → stop for the month
   - Alert sent when budget exhausted
"""

import os
import json
import tempfile
from datetime import date
from loguru import logger
from dataclasses import dataclass, field
from nav_checker import ETF_LIST, NAVChecker

STATE_FILE    = "tranche_state.json"
MONTHLY_FILE  = "monthly_budget.json"
SILVER_FIXED  = 350.0
SILVER_SYMBOL = "ICICISILVER"


def _atomic_write_json(path: str, data: dict):
    """Write JSON atomically: write to a temp file, then rename over the target.
    A crash or power loss mid-write leaves either the old file or the new
    file intact — never a half-written, corrupted one. Plain json.dump()
    straight to the target file (the original approach) does not have this
    guarantee."""
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # atomic on POSIX and Windows
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


@dataclass
class TrancheState:
    date:           str
    bought_today:   bool  = False
    total_invested: float = 0.0
    orders:         list  = field(default_factory=list)


@dataclass
class MonthlyState:
    month:          str
    budget_used:    float
    budget_total:   float
    orders:         list  = field(default_factory=list)

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget_total - self.budget_used)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining <= 0


class AllocationEngine:
    """Allocates the monthly budget across ETFs proportionally."""

    def __init__(self, monthly_budget: float = 1500.0):
        self.monthly_budget = monthly_budget
        self.nav_checker    = NAVChecker()
        self.daily_state    = self._load_daily()
        self.monthly_state  = self._load_monthly()

    def execute(self, triggered_etfs: dict, connector, dry_run=False) -> list:
        """
        triggered_etfs: {symbol: change_pct} — only ETFs that hit their trigger
        e.g. {"SILVERIETF": -7.2, "CPSEETF": -3.1, "MAFANG": -2.4}

        Returns list of executed orders.
        """
        today = str(date.today())
        if self.daily_state.date != today:
            self.daily_state = TrancheState(date=today)
            self._save_daily()

        if self.daily_state.bought_today:
            logger.info("⏭️ Already bought today — skipping")
            return []

        if self.monthly_state.is_exhausted:
            logger.warning(f"🚫 Monthly budget exhausted! ₹{self.monthly_budget} used")
            return []

        remaining = self.monthly_state.remaining
        logger.info(f"💰 Monthly budget remaining: ₹{remaining:.2f}")

        orders = []

        # ── Silver ETF (special fixed allocation) ──────────────────────
        if SILVER_SYMBOL in triggered_etfs:
            silver_pct = triggered_etfs[SILVER_SYMBOL]
            if silver_pct <= -6.5:
                silver_alloc = min(SILVER_FIXED, remaining)
                silver_order = self._buy_etf(SILVER_SYMBOL, silver_alloc, connector, dry_run)
                if silver_order:
                    orders.append(silver_order)
                    remaining -= silver_order["amount"]

        # ── Other ETFs (proportional) ───────────────────────────────────
        other_etfs = {
            sym: pct for sym, pct in triggered_etfs.items()
            if sym != SILVER_SYMBOL and pct <= ETF_LIST[sym]["trigger_pct"]
        }

        if other_etfs and remaining > 0:
            total_fall  = sum(abs(pct) for pct in other_etfs.values())
            allocations = {}
            for sym, pct in other_etfs.items():
                proportion       = abs(pct) / total_fall
                allocations[sym] = round(remaining * proportion, 2)

            # Adjust rounding — make sure total = remaining
            total_alloc = sum(allocations.values())
            diff        = remaining - total_alloc
            if allocations:
                first = list(allocations.keys())[0]
                allocations[first] += diff

            logger.info(f"📊 Proportional allocation: {allocations}")

            for sym, alloc in allocations.items():
                if alloc <= 0:
                    continue
                order = self._buy_etf(sym, alloc, connector, dry_run)
                if order:
                    orders.append(order)

        # ── Update state ────────────────────────────────────────────────
        if orders:
            total_spent = sum(o["amount"] for o in orders)
            self.daily_state.bought_today    = True
            self.daily_state.total_invested += total_spent
            self.daily_state.orders.extend(orders)
            self.monthly_state.budget_used  += total_spent
            self.monthly_state.orders.extend(orders)

            # Save state immediately after each successful order, before
            # returning — if the process crashes right after this point,
            # bought_today is already persisted, preventing a double-buy
            # on restart.
            self._save_daily()
            self._save_monthly()

            logger.success(
                f"✅ Total invested today: ₹{total_spent:.2f} | "
                f"Monthly used: ₹{self.monthly_state.budget_used:.2f} / ₹{self.monthly_budget}"
            )

            if self.monthly_state.is_exhausted:
                logger.warning("🚫 Monthly budget EXHAUSTED — no more buys this month!")

        return orders

    def _buy_etf(self, symbol, amount, connector, dry_run):
        """Buy one ETF with given amount."""
        info = ETF_LIST.get(symbol)
        if not info:
            return None

        ltp = connector.get_ltp(info["exchange"], symbol, info["token"])
        if not ltp:
            logger.error(f"❌ Cannot get price for {symbol}")
            return None

        nav_result = self.nav_checker.check(symbol, ltp)

        if nav_result.action in ("SKIP", "WAIT"):
            logger.warning(f"🚫 {symbol} — {nav_result.reason} — {nav_result.action}")
            return None

        qty = int(amount / ltp)
        if qty <= 0:
            logger.warning(f"⚠️ {symbol} — ₹{amount:.0f} too small for 1 unit @ ₹{ltp:.2f}")
            return None

        actual_cost = qty * ltp
        use_limit   = nav_result.use_limit
        price       = round(ltp * 0.999, 2) if use_limit else None

        logger.info(
            f"🛒 {symbol} x{qty} @ ₹{ltp:.2f} = ₹{actual_cost:.2f} "
            f"| {'LIMIT' if use_limit else 'MARKET'} | {nav_result.reason}"
        )

        if dry_run:
            oid = f"DRY_{symbol}_{int(ltp*100)}"
        else:
            oid = connector.place_buy_order(symbol, info["token"], qty, price, exchange=info["exchange"])

        if oid:
            return {
                "symbol":     symbol,
                "quantity":   qty,
                "price":      ltp,
                "amount":     actual_cost,
                "order_type": "LIMIT" if use_limit else "MARKET",
                "nav":        nav_result.nav,
                "premium":    nav_result.premium_pct,
                "order_id":   oid,
                "date":       str(date.today()),
            }
        return None

    # ── State persistence ───────────────────────────────────────────────

    def _load_daily(self):
        today = str(date.today())
        try:
            with open(STATE_FILE) as f:
                d = json.load(f)
                if d.get("date") == today:
                    return TrancheState(**d)
        except (FileNotFoundError, json.JSONDecodeError, TypeError) as e:
            logger.debug(f"No valid daily state to load ({e}) — starting fresh")
        return TrancheState(date=today)

    def _save_daily(self):
        _atomic_write_json(STATE_FILE, {
            "date":           self.daily_state.date,
            "bought_today":   self.daily_state.bought_today,
            "total_invested": self.daily_state.total_invested,
            "orders":         self.daily_state.orders,
        })

    def _load_monthly(self):
        month = str(date.today())[:7]  # YYYY-MM
        try:
            with open(MONTHLY_FILE) as f:
                d = json.load(f)
                if d.get("month") == month:
                    return MonthlyState(**d)
        except (FileNotFoundError, json.JSONDecodeError, TypeError) as e:
            logger.debug(f"No valid monthly state to load ({e}) — starting fresh")
        return MonthlyState(month=month, budget_used=0.0, budget_total=self.monthly_budget)

    def _save_monthly(self):
        _atomic_write_json(MONTHLY_FILE, {
            "month":        self.monthly_state.month,
            "budget_used":  self.monthly_state.budget_used,
            "budget_total": self.monthly_state.budget_total,
            "orders":       self.monthly_state.orders,
        })
