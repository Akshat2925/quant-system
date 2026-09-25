# 📈 ETF Trading Bot v2.0

> Automatic ETF buying bot for Angel One — upgraded from a single-ETF bot to a smart multi-ETF system.

---

## 🆕 What's New in v2.0

This is a **complete upgrade** of the original [quant-system v1](https://github.com/Akshat2925/quant-system/tree/main) bot.

### v1 → v2 Changes (Plain English)

| What Changed | v1 (Old) | v2 (This) |
|---|---|---|
| **ETFs traded** | Only NIFTYBEES | 5 ETFs — Silver, Gold, Defence, CPSE, Metal |
| **How money is split** | Fixed 33/33/34% tranches | Proportional — ETF that fell more gets more money |
| **Budget system** | Capital % based | Monthly budget (default ₹1500/month) |
| **NAV safety check** | Basic (1 level) | Full SEBI formula — 4 levels (Buy / Limit / Wait / Skip) |
| **Silver ETF** | ❌ Not there | ✅ Special fixed ₹350 rule when it drops -6.5%+ |
| **Market crash detector** | ❌ | ✅ Watches Nifty 50 for market-wide falls |
| **Dashboard** | Very basic | Full Streamlit dashboard with budget progress |
| **Backtest tool** | ❌ | ✅ Test strategy on historical CSV data |
| **Crash recovery** | Bot would die silently | Atomic file writes + crash alerts on Telegram |
| **Error handling** | Basic | Retry logic, safe error recovery, no silent failures |
| **Telegram alerts** | Basic | Detailed — startup, crash, big moves, budget exhaustion |
| **Config validation** | Scattered, no checks | Validates everything at startup, fails with clear error |

---

## 💡 Why This Helps (For Regular Investors)

**The problem:** Manually buying ETFs during market dips means:
- You have to watch the market all day
- You might miss the dip while working/sleeping
- Emotions make you hesitate when prices fall

**What this bot does:**
- Watches 5 ETFs automatically every 5 minutes from 9:15 AM to 3:30 PM
- When an ETF drops past your set trigger (e.g. -2%), it buys automatically in the last 15 minutes of the day (3:00–3:15 PM)
- Spreads your monthly budget smartly — the ETF that fell more gets a bigger share
- Never buys when the ETF is trading at a premium over its actual NAV (so you don't overpay)
- Sends you Telegram alerts for every action

**Result:** You set it up once, and it automatically does SIP-style dip buying for you — without sitting in front of a screen.

---

## 📦 ETFs Covered

| Symbol | ETF Name | Trigger |
|---|---|---|
| `ICICISILVER` | ICICI Prudential Silver ETF | -6.5% |
| `SETFGOLD` | SBI Gold ETF | -2.0% |
| `MODEFENCE` | Motilal Oswal Nifty India Defence ETF | -3.0% |
| `CPSEETF` | CPSE ETF | -2.0% |
| `METALIETF` | ICICI Prudential Nifty Metal ETF | -2.0% |

---

## 🛡️ Safety Features

- ✅ **Once-per-day buy guard** — never double buys, even after a crash/restart
- ✅ **Monthly budget cap** — hard stop, never overspends
- ✅ **NAV premium check** — follows SEBI guidelines, won't buy overpriced ETFs
- ✅ **Atomic file writes** — no data corruption even on power loss
- ✅ **No order retry** — avoids accidental duplicate orders
- ✅ **Dry run mode** — test everything without real money
- ✅ **Telegram crash alerts** — know immediately if something goes wrong

---

## ⚙️ Setup

### 1. Install requirements
```bash
pip install -r requirements.txt
```

### 2. Create `.env` file
```bash
copy .env.example .env
```
Fill in your Angel One credentials:
```
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_PIN=your_4digit_pin
ANGEL_TOTP_SECRET=your_totp_secret
MONTHLY_BUDGET=1500
```

### 3. Create logs folder
```bash
mkdir logs
```

---

## 🚀 How to Run

```bash
# Test connection only (safe)
python bot.py --test

# Dry run — simulates everything, no real orders
python bot.py --dry-run

# Live trading — real orders, real money
python bot.py

# Dashboard
streamlit run dashboard.py

# Backtest on historical data
python backtest.py your_data.csv
```

Or just double-click the desktop shortcuts:
- **`START_BOT_DRY.bat`** — dry run
- **`START_BOT_LIVE.bat`** — live trading

---

## 📊 How Allocation Works

**Monthly budget:** ₹1500 (configurable)

**Example — 3 ETFs trigger on the same day:**

| ETF | Fall | Gets |
|---|---|---|
| CPSEETF | -6% | ₹750 (50%) |
| SETFGOLD | -4% | ₹500 (33%) |
| METALIETF | -2% | ₹250 (17%) |

The bigger the dip, the more you buy — exactly how smart dip-buying should work.

---

## 📁 Project Structure

```
etf-trading-bot/
├── bot.py              # Main bot — scheduling, trigger detection, buy window
├── tranche_engine.py   # Budget allocation + state persistence
├── nav_checker.py      # ETF list + SEBI NAV premium check
├── connector.py        # Angel One SmartAPI wrapper
├── config.py           # Config loader + validation
├── alerts.py           # Telegram alerts
├── market_detector.py  # Nifty 50 market-wide fall detector
├── dashboard.py        # Streamlit status dashboard
├── backtest.py         # Historical simulation tool
├── find_tokens.py      # Utility to find Angel One symbol tokens
└── tests/              # Unit tests (pytest)
```

---

## ⚠️ Disclaimer

This bot places **real orders with real money**. Always test with `--dry-run` first.
Past performance of any strategy does not guarantee future results.
Never invest more than you can afford to lose.

---

## 🔗 Related

- [v1 — Original quant-system](https://github.com/Akshat2925/quant-system) — the single-ETF NIFTYBEES bot this was built from
- [Angel One SmartAPI Docs](https://smartapi.angelbroking.com/docs)
