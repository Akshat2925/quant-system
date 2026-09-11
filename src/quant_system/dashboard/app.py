"""Streamlit dashboard for quant-system.

Displays:
- Live / backtest equity curve
- Open positions with unrealized P&L
- Trade blotter (fills)
- Regime state and macro proxy scores
- Circuit breaker status

Run:
    streamlit run src/quant_system/dashboard/app.py
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import streamlit as st

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Quant System — Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ────────────────────────────────────────────────────────────────
st.sidebar.title("⚙️ Controls")
mode = st.sidebar.selectbox("Mode", ["Live", "Backtest"])
strategy = st.sidebar.selectbox("Strategy", ["Grid — GOLDM", "SAR — NIFTY", "Both"])
regime_state = st.sidebar.selectbox(
    "Regime State",
    ["RISK_ON", "NEUTRAL", "RISK_OFF", "HIGH_VOL", "CIRCUIT_HALT"],
    index=0,
)

st.sidebar.markdown("---")
st.sidebar.markdown("**Risk Controls**")
kill_switch = st.sidebar.toggle("🔴 Global Kill Switch", value=False)
if kill_switch:
    st.sidebar.error("⚠️ Kill switch ACTIVE — no new orders")

st.sidebar.markdown("---")
st.sidebar.caption("GitHub: [Akshat2925/quant-system](https://github.com/Akshat2925/quant-system)")

# ── Header ─────────────────────────────────────────────────────────────────
col_title, col_status = st.columns([3, 1])
with col_title:
    st.title("📈 Quant System Dashboard")
    st.caption(f"Mode: **{mode}** | Strategy: **{strategy}** | Updated: {datetime.now().strftime('%H:%M:%S')}")

regime_colors = {
    "RISK_ON": "🟢",
    "NEUTRAL": "🟡",
    "RISK_OFF": "🟠",
    "HIGH_VOL": "🔴",
    "CIRCUIT_HALT": "⛔",
}
with col_status:
    st.metric("Regime", f"{regime_colors.get(regime_state, '')} {regime_state}")

# ── Generate sample data ────────────────────────────────────────────────────
def _sample_equity(n=120, seed=42):
    rng = random.Random(seed)
    vals = [0.0]
    for _ in range(n - 1):
        vals.append(vals[-1] + rng.gauss(50, 800))
    base = datetime.now(timezone.utc) - timedelta(minutes=n)
    times = [base + timedelta(minutes=i) for i in range(n)]
    return pd.DataFrame({"time": times, "pnl": vals})


def _sample_positions():
    return pd.DataFrame([
        {"Instrument": "GOLDM25DECFUT", "Exchange": "MCX", "Qty": 5, "Avg Price": 72450.00,
         "LTP": 72680.00, "Unrealized P&L": 1150.00, "Pyramid Level": 2},
        {"Instrument": "NIFTY25DECFUT", "Exchange": "NFO", "Qty": -2, "Avg Price": 24120.00,
         "LTP": 24050.00, "Unrealized P&L": 140.00, "Pyramid Level": 0},
    ])


def _sample_blotter():
    rows = []
    instruments = ["GOLDM25DECFUT", "NIFTY25DECFUT"]
    sides = ["BUY", "SELL"]
    base = datetime.now(timezone.utc) - timedelta(hours=4)
    rng = random.Random(99)
    for i in range(12):
        inst = instruments[i % 2]
        side = sides[rng.randint(0, 1)]
        price = rng.uniform(70000, 73000) if "GOLD" in inst else rng.uniform(23800, 24200)
        qty = rng.randint(1, 3)
        pnl = rng.uniform(-500, 1200) if side == "SELL" else None
        rows.append({
            "Time": (base + timedelta(minutes=i * 20)).strftime("%H:%M:%S"),
            "Instrument": inst,
            "Side": side,
            "Qty": qty,
            "Price": round(price, 2),
            "Brokerage": 20.00,
            "STT/CTT": round(price * qty * 0.0001, 2) if side == "SELL" else 0.00,
            "Net P&L": round(pnl, 2) if pnl else "—",
        })
    return pd.DataFrame(rows)


def _sample_macro():
    return pd.DataFrame([
        {"Proxy": "India VIX", "Value": 14.2, "Score": -0.29, "Signal": "Low vol → neutral"},
        {"Proxy": "Market RSI (14)", "Value": 61.3, "Score": 0.23, "Signal": "Bullish momentum"},
        {"Proxy": "Advance/Decline Ratio", "Value": 1.8, "Score": 0.40, "Signal": "Broad participation"},
        {"Proxy": "Market ADX (14)", "Value": 28.5, "Score": 0.14, "Signal": "Trend present"},
        {"Proxy": "OBV Trend", "Value": "Rising", "Score": 0.30, "Signal": "Volume confirms"},
    ])


# ── Section 1: Equity Curve ─────────────────────────────────────────────────
st.markdown("---")
st.subheader("💰 Equity Curve")
eq_df = _sample_equity()
total_pnl = eq_df["pnl"].iloc[-1]
peak = eq_df["pnl"].max()
drawdown = total_pnl - peak

col1, col2, col3, col4 = st.columns(4)
col1.metric("Net P&L", f"₹{total_pnl:,.0f}", delta=f"₹{eq_df['pnl'].diff().iloc[-1]:,.0f}")
col2.metric("Peak P&L", f"₹{peak:,.0f}")
col3.metric("Max Drawdown", f"₹{drawdown:,.0f}")
col4.metric("Total Trades", "12")

st.line_chart(eq_df.set_index("time")["pnl"], height=280, use_container_width=True)

# ── Section 2: Positions ────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📊 Open Positions")
pos_df = _sample_positions()

def _color_pnl(val):
    if isinstance(val, (int, float)):
        color = "color: #27ae60" if val >= 0 else "color: #e74c3c"
        return color
    return ""

st.dataframe(
    pos_df.style.applymap(_color_pnl, subset=["Unrealized P&L"]),
    use_container_width=True,
    hide_index=True,
)

# ── Section 3: Blotter ──────────────────────────────────────────────────────
st.markdown("---")
st.subheader("📋 Trade Blotter")
blotter_df = _sample_blotter()

col_filter1, col_filter2 = st.columns([1, 3])
with col_filter1:
    side_filter = st.selectbox("Filter by side", ["All", "BUY", "SELL"])

filtered = blotter_df if side_filter == "All" else blotter_df[blotter_df["Side"] == side_filter]
st.dataframe(filtered, use_container_width=True, hide_index=True)

# ── Section 4: Regime Engine ────────────────────────────────────────────────
st.markdown("---")
st.subheader("🌐 Macro Regime Engine")
macro_df = _sample_macro()

col_regime, col_macro = st.columns([1, 2])
with col_regime:
    color_map = {
        "RISK_ON": "#27ae60",
        "NEUTRAL": "#f39c12",
        "RISK_OFF": "#e67e22",
        "HIGH_VOL": "#e74c3c",
        "CIRCUIT_HALT": "#8e44ad",
    }
    bg = color_map.get(regime_state, "#333")
    st.markdown(
        f"""
        <div style="background:{bg};padding:30px;border-radius:12px;text-align:center;">
            <h1 style="color:white;margin:0;font-size:2rem;">{regime_colors.get(regime_state,'')} {regime_state}</h1>
            <p style="color:rgba(255,255,255,0.8);margin:8px 0 0 0;">Current Regime Classification</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

with col_macro:
    st.dataframe(macro_df, use_container_width=True, hide_index=True)

# ── Section 5: Circuit Breaker Status ──────────────────────────────────────
st.markdown("---")
st.subheader("🛡️ Risk & Circuit Breakers")

cb_col1, cb_col2, cb_col3, cb_col4 = st.columns(4)
cb_col1.metric("Daily Loss Limit", "₹50,000", delta="Used: ₹4,320")
cb_col2.metric("Max Drawdown", "₹1,00,000", delta="Used: ₹6,200")
cb_col3.metric("Orders/Min", "60 max", delta="Current: 3")
cb_col4.metric("Kill Switch", "🔴 ACTIVE" if kill_switch else "🟢 OFF")

if regime_state == "CIRCUIT_HALT":
    st.error("⛔ CIRCUIT HALT — All new order placement is blocked. position_cap_lots = 0.")
elif kill_switch:
    st.error("🔴 Global kill switch is active. No new orders will be placed.")
else:
    st.success("✅ All circuit breakers nominal. System operating normally.")

# ── Footer ──────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption("quant-system v0.1.0 | Grid / Stop-and-Reverse execution engine for MCX/NSE | [GitHub](https://github.com/Akshat2925/quant-system)")
