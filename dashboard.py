import json
from datetime import date
from pathlib import Path

import streamlit as st
import pandas as pd

st.set_page_config(page_title="ETF Trading Bot", layout="wide")
st.title("📈 ETF Trading Bot Dashboard")
st.caption(f"Today: {date.today().strftime('%d %B %Y')}")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


daily = load_json("tranche_state.json", {
    "date": str(date.today()), "bought_today": False,
    "total_invested": 0.0, "orders": []
})
monthly = load_json("monthly_budget.json", {
    "month": str(date.today())[:7], "budget_used": 0.0,
    "budget_total": 1500.0, "orders": []
})

c1, c2, c3, c4 = st.columns(4)
c1.metric("💰 Invested Today", f"₹{daily['total_invested']:,.2f}")
c2.metric("📦 Orders Today", len(daily["orders"]))
c3.metric("Today's Buy", "✅ Done" if daily["bought_today"] else "⏳ Waiting")

remaining = max(0.0, monthly["budget_total"] - monthly["budget_used"])
c4.metric("💵 Monthly Remaining", f"₹{remaining:,.2f}")

st.progress(min(1.0, monthly["budget_used"] / monthly["budget_total"]) if monthly["budget_total"] else 0.0)
st.caption(f"Monthly budget used: ₹{monthly['budget_used']:,.2f} / ₹{monthly['budget_total']:,.2f}")

st.markdown("---")
st.subheader("📋 Today's Orders")
if daily["orders"]:
    df = pd.DataFrame(daily["orders"])
    if "price" in df:
        df["price"] = df["price"].map(lambda x: f"₹{x:.2f}")
    if "amount" in df:
        df["amount"] = df["amount"].map(lambda x: f"₹{x:,.2f}")
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No orders yet — bot is watching... 👀")

st.markdown("---")
st.subheader("📅 This Month's Orders")
if monthly["orders"]:
    dfm = pd.DataFrame(monthly["orders"])
    if "price" in dfm:
        dfm["price"] = dfm["price"].map(lambda x: f"₹{x:.2f}")
    if "amount" in dfm:
        dfm["amount"] = dfm["amount"].map(lambda x: f"₹{x:,.2f}")
    st.dataframe(dfm, use_container_width=True, hide_index=True)
else:
    st.info("No orders this month yet.")

st.markdown("---")
st.subheader("📜 Recent Logs")
logs = sorted(Path("logs").glob("bot_*.log"), reverse=True) if Path("logs").exists() else []
if logs:
    lines = open(logs[0]).readlines()[-40:]
    st.code("".join(lines))
else:
    st.info("Start the bot to see logs")
