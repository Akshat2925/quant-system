"""
Backtest the trigger + allocation logic against historical daily price data.

This does NOT call the live broker API or the NAV premium check (no
historical NAV feed available for free) — it tests the CORE decision logic:
given a day's open and close price for each ETF, would a trigger have
fired, and how would the budget have been allocated?

This is intentionally simple. It is not a substitute for months of
--dry-run paper trading against live data, which is what you should also
do before trusting this with real money. Treat this as a sanity check on
the allocation math, not a performance guarantee — past price patterns
are not a predictor of future ones.

INPUT FORMAT (CSV), one row per trading day per symbol:
    date,symbol,open,close
    2026-01-02,CPSEETF,90.0,88.5
    2026-01-02,SETFGOLD,120.0,121.0
    ...

USAGE:
    python backtest.py historical_prices.csv
"""

import sys
import csv
from collections import defaultdict

from nav_checker import ETF_LIST

MONTHLY_BUDGET = 1500.0
SILVER_SYMBOL  = "ICICISILVER"
SILVER_FIXED   = 350.0


def load_csv(path):
    """Returns {date: {symbol: (open, close)}}"""
    by_date = defaultdict(dict)
    with open(path) as f:
        for row in csv.DictReader(f):
            by_date[row["date"]][row["symbol"]] = (float(row["open"]), float(row["close"]))
    return by_date


def simulate(by_date, monthly_budget=MONTHLY_BUDGET):
    results = []
    month_used = defaultdict(float)

    for day in sorted(by_date):
        month = day[:7]
        if month_used[month] >= monthly_budget:
            continue  # budget exhausted for the month

        prices = by_date[day]
        triggered = {}
        for sym, (open_p, close_p) in prices.items():
            info = ETF_LIST.get(sym)
            if not info:
                continue
            change_pct = ((close_p - open_p) / open_p) * 100
            if change_pct <= info["trigger_pct"]:
                triggered[sym] = change_pct

        if not triggered:
            continue

        remaining = monthly_budget - month_used[month]
        day_orders = []

        if SILVER_SYMBOL in triggered and triggered[SILVER_SYMBOL] <= -6.5:
            alloc = min(SILVER_FIXED, remaining)
            _, close_p = prices[SILVER_SYMBOL]
            qty = int(alloc / close_p)
            if qty > 0:
                spent = qty * close_p
                day_orders.append((SILVER_SYMBOL, qty, close_p, spent))
                remaining -= spent

        others = {s: p for s, p in triggered.items() if s != SILVER_SYMBOL}
        if others and remaining > 0:
            total_fall = sum(abs(p) for p in others.values())
            for sym, pct in others.items():
                alloc = remaining * (abs(pct) / total_fall)
                _, close_p = prices[sym]
                qty = int(alloc / close_p)
                if qty > 0:
                    spent = qty * close_p
                    day_orders.append((sym, qty, close_p, spent))

        day_spent = sum(o[3] for o in day_orders)
        if day_spent > 0:
            month_used[month] += day_spent
            results.append({"date": day, "orders": day_orders, "spent": day_spent})

    return results


def print_report(results, monthly_budget):
    if not results:
        print("No trigger events found in this data.")
        return

    total_spent = sum(r["spent"] for r in results)
    print(f"\n{'='*50}\nBACKTEST REPORT\n{'='*50}")
    print(f"Trading days with a buy: {len(results)}")
    print(f"Total invested: ₹{total_spent:,.2f}\n")

    for r in results:
        print(f"{r['date']} — spent ₹{r['spent']:.2f}")
        for sym, qty, price, spent in r["orders"]:
            print(f"    {sym}: {qty} units @ ₹{price:.2f} = ₹{spent:.2f}")

    print(f"\nNOTE: This simulates the trigger/allocation math only — it does not "
          f"model NAV premium checks, slippage, order rejections, or broker fees. "
          f"Run --dry-run against live data for months before trusting real capital.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python backtest.py historical_prices.csv")
        sys.exit(1)

    data = load_csv(sys.argv[1])
    results = simulate(data)
    print_report(results, MONTHLY_BUDGET)
