"""
CLI tools — Stage 12.

Provides --check, --portfolio, --reconcile commands for bot.py.
All read-only. No orders placed.
"""

from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

IST = ZoneInfo("Asia/Kolkata")


def run_check(config, angel_connector, groww_connector=None) -> bool:
    """
    Full health check. Returns True if all critical items pass.
    Prints PASS/WARN/FAIL per item.
    """
    print("\n" + "=" * 50)
    print("  HEALTH CHECK")
    print("=" * 50)
    all_pass = True

    # 1. Config
    try:
        print(f"  {'PASS':5s} Config loaded — MODE={config.mode}")
    except Exception as e:
        print(f"  {'FAIL':5s} Config error: {e}")
        all_pass = False

    # 2. Angel One login
    try:
        ok = angel_connector.login()
        if ok:
            print(f"  {'PASS':5s} Angel One login OK")
        else:
            print(f"  {'FAIL':5s} Angel One login FAILED")
            all_pass = False
    except Exception as e:
        print(f"  {'FAIL':5s} Angel One login error: {e}")
        all_pass = False

    # 3. Angel price fetch
    try:
        from nav_checker import ETF_LIST
        sym  = list(ETF_LIST.keys())[0]
        info = ETF_LIST[sym]
        ltp, open_ = angel_connector.get_quote(info["exchange"], sym, info["token"])
        if ltp:
            print(f"  {'PASS':5s} Angel price fetch OK ({sym} Rs.{ltp:.2f})")
        else:
            print(f"  {'WARN':5s} Angel price fetch returned None for {sym}")
    except Exception as e:
        print(f"  {'WARN':5s} Angel price fetch error: {e}")

    # 4. Angel funds
    try:
        funds = angel_connector.get_funds()
        if funds is not None:
            print(f"  {'PASS':5s} Angel funds OK — Rs.{funds:,.2f}")
        else:
            print(f"  {'WARN':5s} Angel funds returned None")
    except Exception as e:
        print(f"  {'WARN':5s} Angel funds error: {e}")

    # 5. Groww (if configured)
    if groww_connector:
        try:
            ok = groww_connector.login()
            if ok:
                holdings = groww_connector.get_holdings()
                print(f"  {'PASS':5s} Groww OK — {len(holdings)} holdings")
            else:
                print(f"  {'WARN':5s} Groww login failed")
        except Exception as e:
            print(f"  {'WARN':5s} Groww error: {e}")
    else:
        print(f"  {'WARN':5s} Groww not configured (GROWW_API_KEY missing)")

    # 6. State files
    import os
    for sf in ["tranche_state.json", "monthly_budget.json"]:
        if os.path.exists(sf):
            print(f"  {'PASS':5s} State file exists: {sf}")
        else:
            print(f"  {'WARN':5s} State file not found: {sf} (will be created on first run)")

    # 7. Mode
    mode_ok = config.mode in ("alert_only", "dry_run", "live")
    print(f"  {'PASS' if mode_ok else 'FAIL':5s} MODE={config.mode}")

    # 8. IP check (live only)
    if config.mode == "live" and getattr(config, "registered_static_ip", ""):
        from ip_guard import check_ip
        ip_result = check_ip(config.registered_static_ip)
        status = "PASS" if ip_result.match else "WARN"
        print(f"  {status:5s} IP check: {ip_result.reason[:60]}")

    print("=" * 50)
    print(f"  Result: {'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED/WARNED'}")
    print("=" * 50 + "\n")
    return all_pass


def run_portfolio(config, angel_connector, groww_connector=None, csv_output=False):
    """Print unified portfolio table."""
    print("\n📊 Portfolio")
    print(f"   As of: {datetime.now(IST).strftime('%d %b %Y %H:%M IST')}")
    print(f"   Mode:  {config.mode}\n")

    try:
        from watchlist import build as build_watchlist
        from prices import PriceProvider
        from portfolio import build as build_portfolio

        wl = build_watchlist(groww_connector=groww_connector,
                             angel_connector=angel_connector)
        pp = PriceProvider(angel_connector, groww_connector)
        pr = pp.get_all(wl)
        summary = build_portfolio(wl, pr)

        if csv_output:
            print("symbol,qty_groww,qty_angel,qty_total,avg_price,invested,current_price,current_value,pnl_rs,pnl_pct,weight_pct")
            for item in summary.items:
                d = item.to_dict()
                print(f"{d['symbol']},{d['qty_groww']},{d['qty_angel']},{d['qty_total']},"
                      f"{d['avg_price']},{d['invested']},{d['current_price'] or ''},"
                      f"{d['current_value'] or ''},{d['unrealised_pnl'] or ''},"
                      f"{d['unrealised_pct'] or ''},{d['weight_pct'] or ''}")
        else:
            fmt = "{:<20s} {:>8s} {:>8s} {:>10s} {:>12s} {:>12s} {:>10s} {:>8s}"
            print(fmt.format("Symbol", "Qty", "AvgPx", "LTP", "Invested", "Value", "P&L%", "Wt%"))
            print("-" * 95)
            for item in summary.items:
                d = item.to_dict()
                pnl_pct = f"{d['unrealised_pct']:+.1f}%" if d['unrealised_pct'] is not None else "N/A"
                wt      = f"{d['weight_pct']:.1f}%" if d['weight_pct'] is not None else "N/A"
                ltp_s   = f"Rs.{d['current_price']:.2f}" if d['current_price'] else "N/A"
                val_s   = f"Rs.{d['current_value']:.0f}" if d['current_value'] else "N/A"
                print(fmt.format(
                    d['symbol'][:20],
                    f"{d['qty_total']:.0f}",
                    f"Rs.{d['avg_price']:.2f}",
                    ltp_s,
                    f"Rs.{d['invested']:.0f}",
                    val_s,
                    pnl_pct,
                    wt,
                ))
            print("-" * 95)
            print(f"{'TOTAL':20s} {'':>8s} {'':>8s} {'':>10s} "
                  f"Rs.{summary.total_invested:>10.0f} "
                  f"{'Rs.' + str(round(summary.total_value)) if summary.total_value else 'N/A':>12s} "
                  f"{'%+.1f%%' % summary.total_pnl_pct if summary.total_pnl_pct else 'N/A':>10s}")
            print(f"\nData as of {datetime.now(IST).strftime('%H:%M IST')} — price source: Angel One\n")

    except Exception as e:
        print(f"Error building portfolio: {e}")


def run_reconcile(config, angel_connector, groww_connector=None):
    """Print reconciliation report."""
    print("\n🔍 Reconciliation Report")
    print(f"   As of: {datetime.now(IST).strftime('%d %b %Y %H:%M IST')}\n")

    try:
        from watchlist import build as build_watchlist
        from reconcile import diff, SNAPSHOT_FILE
        import os

        wl = build_watchlist(groww_connector=groww_connector,
                             angel_connector=angel_connector)

        if not os.path.exists(SNAPSHOT_FILE):
            print("  No snapshot found — run once to create baseline.")
            return

        events = diff(wl)
        if not events:
            print("  No changes detected since last snapshot.")
        else:
            print(f"  {len(events)} change(s) detected:")
            for e in events:
                sign = "+" if e["type"] == "purchase" else "-"
                amt  = f"  ~Rs.{e['est_amount']:.0f}" if e.get("est_amount") else ""
                print(f"    {sign}{e['qty_change']:.0f} {e['symbol']} ({e['broker']}){amt}")

        print()

    except Exception as e:
        print(f"Error in reconcile: {e}")
