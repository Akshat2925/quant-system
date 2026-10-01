"""
ETF Trading Bot — Streamlit status dashboard.

Reads state files directly (no live broker connection).
Supports both dry-run and live state via a sidebar selector.
"""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="ETF Bot Dashboard", layout="wide")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load(path: str, default: dict) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _fmt_inr(val) -> str:
    try:
        return f"₹{float(val):,.2f}"
    except (TypeError, ValueError):
        return "—"


# ── Mode selector ─────────────────────────────────────────────────────────────

st.sidebar.title("⚙️ Settings")
mode = st.sidebar.radio(
    "Bot mode",
    options=["live", "dry"],
    format_func=lambda m: "💰 Live" if m == "live" else "🧪 Dry Run",
)

if mode == "dry":
    state_file   = "tranche_state.dry.json"
    monthly_file = "monthly_budget.dry.json"
    st.sidebar.info("Showing DRY RUN state — no real orders here.")
else:
    state_file   = "tranche_state.json"
    monthly_file = "monthly_budget.json"

if st.sidebar.button("🔄 Refresh now"):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption("Dashboard reads state files directly. Refresh to see latest data.")

# ── Load state ────────────────────────────────────────────────────────────────

today = str(date.today())
month = today[:7]

daily_default = {
    "date": today, "bought_today": False,
    "total_invested": 0.0, "bought_symbols": [], "orders": [],
}
monthly_default = {
    "month": month, "budget_used": 0.0,
    "budget_total": 1500.0, "orders": [],
}

daily   = _load(state_file,   daily_default)
monthly = _load(monthly_file, monthly_default)

# Only show data that is actually for today / this month
if daily.get("date") != today:
    daily = daily_default
if monthly.get("month") != month:
    monthly = monthly_default

# ── Header ────────────────────────────────────────────────────────────────────

mode_badge = "🧪 DRY RUN" if mode == "dry" else "💰 LIVE"
st.title(f"📈 ETF Trading Bot Dashboard — {mode_badge}")
st.caption(f"Today: {date.today().strftime('%d %B %Y')}")

# ── KPI row ───────────────────────────────────────────────────────────────────

c1, c2, c3, c4 = st.columns(4)
c1.metric("💰 Invested Today",  _fmt_inr(daily.get("total_invested", 0)))
c2.metric("📦 Orders Today",    len(daily.get("orders", [])))
c3.metric("Buy Status",
          "✅ Done" if daily.get("bought_today") else "⏳ Waiting")

budget_total = float(monthly.get("budget_total") or 1500)
budget_used  = float(monthly.get("budget_used")  or 0)
remaining    = max(0.0, budget_total - budget_used)
c4.metric("💵 Monthly Remaining", _fmt_inr(remaining))

# Budget progress bar
pct = budget_used / budget_total if budget_total else 0.0
st.progress(min(1.0, pct))
st.caption(
    f"Monthly budget used: {_fmt_inr(budget_used)} / {_fmt_inr(budget_total)} "
    f"({pct*100:.1f}%)"
)

st.markdown("---")

# ── Today's orders ────────────────────────────────────────────────────────────

st.subheader("📋 Today's Orders")
today_orders = daily.get("orders", [])

if today_orders:
    df = pd.DataFrame(today_orders)

    # Friendly column formatting
    for col in ("price", "nav"):
        if col in df.columns:
            df[col] = df[col].apply(lambda x: _fmt_inr(x) if x else "—")
    if "amount" in df.columns:
        df["amount"] = df["amount"].apply(_fmt_inr)
    if "premium" in df.columns:
        df["premium"] = df["premium"].apply(
            lambda x: f"{float(x):+.2f}%" if x is not None else "—"
        )
    if "fill_status" in df.columns:
        df["fill_status"] = df["fill_status"].apply(
            lambda s: "✅ complete" if s in ("complete", "filled", "dry_run")
            else f"⚠️ {s}"
        )

    # Column order: most useful first
    preferred = ["symbol", "quantity", "price", "amount",
                 "order_type", "fill_status", "premium", "nav", "order_id"]
    cols = [c for c in preferred if c in df.columns]
    st.dataframe(df[cols], use_container_width=True, hide_index=True)
else:
    st.info("No orders today — bot is watching... 👀")

st.markdown("---")

# ── This month's orders ───────────────────────────────────────────────────────

st.subheader("📅 This Month's Orders")
month_orders = monthly.get("orders", [])

if month_orders:
    dfm = pd.DataFrame(month_orders)
    for col in ("price", "nav"):
        if col in dfm.columns:
            dfm[col] = dfm[col].apply(lambda x: _fmt_inr(x) if x else "—")
    if "amount" in dfm.columns:
        dfm["amount"] = dfm["amount"].apply(_fmt_inr)
    if "fill_status" in dfm.columns:
        dfm["fill_status"] = dfm["fill_status"].apply(
            lambda s: "✅ complete" if s in ("complete", "filled", "dry_run")
            else f"⚠️ {s}"
        )
    preferred = ["date", "symbol", "quantity", "price", "amount",
                 "order_type", "fill_status", "premium"]
    cols = [c for c in preferred if c in dfm.columns]
    st.dataframe(dfm[cols], use_container_width=True, hide_index=True)
else:
    st.info("No orders this month yet.")

st.markdown("---")

# ── Recent logs ───────────────────────────────────────────────────────────────

st.subheader("📜 Recent Logs")
log_dir = Path("logs")
logs    = sorted(log_dir.glob("bot_*.log"), reverse=True) if log_dir.exists() else []
if logs:
    with open(logs[0]) as fh:
        lines = fh.readlines()[-50:]
    st.code("".join(lines), language="text")
else:
    st.info("Start the bot to see logs.")
