# ETF Trading Bot v2.0 🤖

> Automated ETF dip-buying and alert system for **Angel One (India)** using SmartAPI.
> Watches 5 ETFs every 5 minutes during market hours and either sends a **BUY SIGNAL** on Telegram or places a real LIMIT order — depending on your chosen mode.

---

## ⚠️ Important Disclaimers

- This bot places **REAL orders with REAL money** in live mode.
- Always run in `alert_only` mode first and verify signals before enabling live orders.
- Past performance does not guarantee future results. Never invest more than you can afford to lose.
- The NAV bands used are personal thresholds, not SEBI mandates.

---

## Operating Modes

| Mode | What happens | Orders placed? |
|------|-------------|---------------|
| `alert_only` | Computes signals, sends **BUY SIGNAL** on Telegram | ❌ Never |
| `dry_run` | Full simulation with fake order IDs, separate state files | ❌ Never |
| `live` | Real LIMIT orders on Angel One | ✅ Yes — real money |

**Default is `alert_only`** — safe to run without risk.

Set in `.env`:
```
MODE=alert_only    # default — recommended for testing
MODE=dry_run       # simulate full flow
MODE=live          # real orders (requires static IP + confirmation)
```

---

## ETFs Monitored

| Angel Symbol | ETF Name | Trigger | Special Rule |
|---|---|---|---|
| `SILVERIETF-EQ` | ICICI Prudential Silver ETF | −6.5% | Fixed ₹350 allocation |
| `SETFGOLD-EQ` | SBI Gold ETF | −2.0% | Proportional |
| `MODEFENCE-EQ` | Motilal Oswal Nifty India Defence ETF | −3.0% | Proportional |
| `CPSEETF-EQ` | CPSE ETF | −2.0% | Proportional |
| `METALIETF-EQ` | ICICI Prudential Nifty Metal ETF | −2.0% | Proportional |

> **Note:** Angel One NSE cash segment requires the `-EQ` suffix. Bare symbols like `CPSEETF` or `ICICISILVER` return no data from ltpData.

---

## Telegram Alerts

Every significant event sends a Telegram message tagged `[ALERT ONLY]`, `[DRY RUN]`, or `[LIVE]`.

| Event | When |
|-------|------|
| `BOT STARTED` | Startup — watchlist prices, budget status |
| `HEARTBEAT` | 9:20 AM — bot is alive |
| `TRIGGER HIT` | ETF crosses its dip threshold |
| `TRIGGER ESCALATION` | Fall deepens by another 1% |
| `TRIGGER RECOVERED` | ETF recovers before buy window |
| `BUY WINDOW OPEN` | 3:00 PM — planned allocations |
| `BUY SIGNAL` | alert_only: suggested qty, LIMIT price, amount |
| `BUY SKIPPED` | Triggered ETF not bought + exact reason |
| `ORDER FILLED` | live/dry_run: confirmed fill with avg price |
| `ORDER REJECTED` | Broker rejected — budget NOT charged |
| `BUDGET LOW` | < 20% monthly budget remaining |
| `BUDGET EXHAUSTED` | Monthly budget fully used |
| `DAILY SUMMARY` | 3:35 PM — full day recap |
| Error events | Login fail, price feed down, state corrupt |

**Commands (send from your configured chat):**
- `/status` — current watchlist prices + budget

Auto-reply to any other message: "This is a one-way alert bot."

---

## How Allocation Works

**Monthly budget:** ₹1500 (configurable)
**Daily cap:** 33% = max ₹495/day

Example — 3 ETFs trigger on the same day:

| ETF | Fall | Gets |
|-----|------|------|
| CPSEETF-EQ | −6% | ₹247 (50%) |
| SETFGOLD-EQ | −4% | ₹165 (33%) |
| METALIETF-EQ | −2% | ₹83 (17%) |

Silver gets a fixed ₹350 when it falls ≥ 6.5%.

---

## Setup

### 1. Install requirements
```bash
pip install -r requirements.txt
```

### 2. Create `.env`
```bash
copy .env.example .env
```
Fill in:
```
ANGEL_API_KEY=your_key
ANGEL_CLIENT_ID=A123456
ANGEL_PIN=1234
ANGEL_TOTP_SECRET=YOURBASE32SECRET

MODE=alert_only
MONTHLY_BUDGET=1500
DAILY_CAP_PCT=0.33
NAV_CHECK_MODE=advisory

TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Test connection
```bash
python bot.py --test
```

### 4. Test Telegram
```bash
python bot.py --test-telegram
```

---

## Running

```bash
# Safe — signals only, no orders (RECOMMENDED to start)
python bot.py --alert-only

# Simulate full flow, no real orders
python bot.py --dry-run

# Live trading (real orders)
# Must set MODE=live in .env first
python bot.py --live

# Dashboard
streamlit run dashboard.py
```

**Desktop shortcuts:**
- `START_BOT_ALERT.bat` — alert_only (safe)
- `START_BOT_DRY.bat` — dry run
- `START_BOT_LIVE.bat` — live (fails if MODE≠live in .env)

---

## Windows Task Scheduler

```
Trigger : Daily, weekdays, 09:10 AM
Action  : python bot.py
Start in: D:\etf-trading-bot
Settings: Wake computer, don't start if already running
```

The bot exits automatically at **15:35 IST** after sending daily summary.

---

## Safety Features

- ✅ `alert_only` default — zero order API calls until you explicitly set `MODE=live`
- ✅ Live mode requires interactive confirmation (`YES` typed) or `--confirm-live`
- ✅ LIMIT orders only (no MARKET, no IOC) — Angel One algo rules
- ✅ Once-per-symbol, once-per-day buy guard (survives crash + restart)
- ✅ Daily spending cap (33% of monthly budget per day)
- ✅ Monthly budget hard cap
- ✅ Daily loss limit
- ✅ NAV fail-safe (unavailable/stale NAV → WAIT, never blind-buy)
- ✅ Order fill verification (only filled orders counted)
- ✅ Atomic JSON state writes (no corruption on crash)
- ✅ Separate dry/live state files
- ✅ Auto re-login on session expiry
- ✅ IST timezone, holiday + weekend guard
- ✅ Token masked in all logs

---

## Project Status (Work in Progress)

| Stage | Status | Description |
|-------|--------|-------------|
| Stage 0 | ✅ Done | Audit + symbol fix (`-EQ` suffix, `SILVERIETF`) |
| Stage 1 | ✅ Done | MODE system (alert_only/dry_run/live) |
| Stage 2 | 🔄 In progress | Static IP guard, LIMIT-only orders |
| Stage 3 | ⏳ Planned | Groww connector (read-only holdings) |
| Stage 4 | ⏳ Planned | Instrument matching (Groww ↔ Angel) |
| Stage 5 | ⏳ Planned | Dynamic watchlist from real holdings |
| Stage 6 | ⏳ Planned | Price provider with fallback |
| Stage 7 | ⏳ Planned | Unified portfolio model |
| Stage 8+ | ⏳ Planned | Signals, sell alerts, tools, docs |

---

## File Structure

```
etf-trading-bot/
├── bot.py              # Main bot — scheduling, signals, buy window
├── tranche_engine.py   # Budget allocation, state, order management
├── nav_checker.py      # ETF list (with correct -EQ symbols) + NAV check
├── connector.py        # Angel One SmartAPI wrapper
├── notifier.py         # Full Telegram notification system
├── ip_guard.py         # Static IP verification for live mode
├── config.py           # Config loader + NSE holiday list
├── alerts.py           # Legacy alert wrapper (kept for compatibility)
├── market_detector.py  # Nifty 50 market-wide fall alert
├── dashboard.py        # Streamlit status dashboard
├── backtest.py         # Offline CSV-based simulation
├── find_tokens.py      # Utility: find Angel One symbol tokens
├── START_BOT_ALERT.bat # Desktop launcher — alert_only (safe)
├── START_BOT_DRY.bat   # Desktop launcher — dry run
├── START_BOT_LIVE.bat  # Desktop launcher — live (checks .env MODE)
├── start_bot_task.bat  # Task Scheduler launcher
└── tests/              # pytest suite (82 tests, all mocked)
```

---

## Running Tests

```bash
pytest tests/ -v
```
82 tests, 0 failures, no network or real credentials needed.

---

## Security Notes

- Never commit `.env` — it contains real credentials
- Never share API keys, PIN, or TOTP secret
- If any credential is exposed, rotate it immediately at Angel One
- Telegram token is never logged (masked as `12345678…`)
