# Project Status

_Last verified: 2026-09-30 against code, tests, and a live pytest run._
_Update this file whenever a stage is completed or a bug is fixed._

---

## Test suite

```
pytest tests/ -v   →   82 passed, 0 failed, 2 deprecation warnings (SmartConnect SSL — third-party library, not our code)
```

---

## What is implemented today

### Core bot loop (`bot.py`)

| Capability | Status | Notes |
|---|---|---|
| 5-minute polling loop during market hours | ✅ Done | `schedule` library |
| IST timezone throughout | ✅ Done | `zoneinfo.ZoneInfo("Asia/Kolkata")` |
| Weekday + NSE holiday guard | ✅ Done | `_is_trading_day()` in `bot.py` |
| Day-open price from broker API on every cycle | ✅ Done | `connector.get_quote()` → ltpData `open` field |
| Intraday dip detection per ETF | ✅ Done | Per-ETF configurable thresholds |
| Trigger alert (first time, then escalation) | ✅ Done | Deduped per symbol per day |
| Trigger-recovered alert | ✅ Done | Fires once per symbol if ETF recovers before buy window |
| Buy window detection | ✅ Done | Configurable time window |
| EOD auto-exit with daily summary | ✅ Done | Exits at 15:35 IST |
| Heartbeat alert at market open | ✅ Done | 9:20 AM IST |
| Session re-login on token expiry | ✅ Done | `connector._try_relogin()` |

### Operating modes (`MODE`)

| Mode | Status | Notes |
|---|---|---|
| `alert_only` (default) | ✅ Done | Zero order API calls; sends BUY SIGNAL to Telegram |
| `dry_run` | ✅ Done | Fake order IDs; separate state files |
| `live` | ✅ Done | Real LIMIT orders; requires explicit confirmation |
| CLI flag override (`--alert-only`, `--dry-run`, `--live`) | ✅ Done | Overrides `.env` MODE |
| Interactive confirmation before live mode | ✅ Done | Typed `YES` or `--confirm-live` flag |

### Allocation engine (`tranche_engine.py`)

| Capability | Status | Notes |
|---|---|---|
| Budget-aware proportional allocation | ✅ Done | Larger dip gets larger share |
| Special fixed allocation for one ETF category | ✅ Done | Configurable; only when fall exceeds threshold |
| Daily spending cap | ✅ Done | `DAILY_CAP_PCT` |
| Monthly budget cap + rollover | ✅ Done | Resets on new calendar month |
| Daily loss limit guard | ✅ Done | Stops buying if daily spend exceeds limit |
| Per-symbol once-per-day guard | ✅ Done | Persisted; survives crash and restart |
| Atomic state file writes | ✅ Done | temp + fsync + rename |
| Separate state for dry vs live | ✅ Done | `.dry.json` vs `.json` |
| Corrupt state detection and backup | ✅ Done | Backed up as `.corrupt`; live orders blocked |
| Rounding carry-back | ✅ Done | Unspent fraction passed to next ETF |
| Order fill verification (live) | ✅ Done | Polls orderBook; only filled orders counted |
| LIMIT order auto-cancel on timeout | ✅ Done | Cancelled after poll window if still open |
| Rejected/unfilled orders not counted | ✅ Done | Budget and bought-today flags not set |

### NAV premium check (`nav_checker.py`)

| Capability | Status | Notes |
|---|---|---|
| Fetch declared NAV from mfapi.in | ✅ Done | End-of-day NAV (not live iNAV) |
| Staleness guard (> 3 days old → WAIT) | ✅ Done | |
| Advisory mode | ✅ Done | Only hard-blocks above a fixed threshold |
| Strict mode | ✅ Done | Respects per-ETF max_premium as WAIT threshold |
| NAV unavailable → WAIT (fail-safe) | ✅ Done | Never buys blind |
| Live iNAV feed | ❌ Not implemented | Would need a different data source |

### Angel One connector (`connector.py`)

| Capability | Status | Notes |
|---|---|---|
| TOTP-based login | ✅ Done | `pyotp` |
| Exponential-backoff retry for data calls | ✅ Done | 3 attempts max |
| `get_quote()` returning (ltp, day_open) | ✅ Done | From ltpData |
| `get_funds()` returning None on failure | ✅ Done | Never returns 0.0 on error |
| `place_buy_order()` — LIMIT only | ✅ Done | No MARKET/IOC path |
| No automatic order retry | ✅ Done | By design — avoids duplicate orders |
| Order status polling | ✅ Done | `get_order_status()` |
| Order cancellation | ✅ Done | `cancel_order()` |
| Re-login on consecutive failures | ✅ Done | After 3 consecutive quote failures |

### Telegram notifications (`notifier.py`)

| Capability | Status | Notes |
|---|---|---|
| Non-blocking background send queue | ✅ Done | Daemon thread |
| Retry with exponential backoff | ✅ Done | 3 attempts |
| 429 rate-limit handling | ✅ Done | Respects `retry_after` |
| Dual chat-ID support | ✅ Done | `TELEGRAM_CHAT_ID_2` |
| Per-day dedupe persisted across restarts | ✅ Done | `notifier_dedupe[.dry].json` |
| Error event rate-limiting (max 1/30 min) | ✅ Done | Per event type |
| Token masked in all logs | ✅ Done | First 8 chars + `…` |
| Mode tag in every message | ✅ Done | `[ALERT ONLY]` / `[DRY RUN]` / `[LIVE]` |
| `/status` command | ✅ Done | Read-only; only from configured chat ID |
| Auto-reply to other messages | ✅ Done | "This is a one-way alert bot" |
| All 17 event types | ✅ Done | See `E.*` constants in `notifier.py` |
| `--test-telegram` CLI flag | ✅ Done | Sends sample of every event type |
| `--simulate-trigger` CLI flag | ✅ Done | Injects fake trigger (alert/dry modes only) |

### Dashboard (`dashboard.py`)

| Capability | Status | Notes |
|---|---|---|
| Live/dry mode selector | ✅ Done | Sidebar radio button |
| Budget progress bar | ✅ Done | |
| Today's orders table | ✅ Done | With fill status |
| Monthly orders table | ✅ Done | |
| Recent log viewer | ✅ Done | Last 50 lines |
| Auto-refresh | ❌ Not implemented | Manual refresh button only |
| Live price feed | ❌ Not implemented | Reads state files only |

### Static IP guard (`ip_guard.py`)

| Capability | Status | Notes |
|---|---|---|
| IP fetch (two fallback services) | ✅ Done | Code exists in `ip_guard.py` |
| IP match check | ✅ Done | Code exists in `ip_guard.py` |
| **Integrated into live order flow** | ❌ NOT INTEGRATED | `ip_guard.py` is never imported or called. Live orders can be placed from any IP. |

### Backtest (`backtest.py`)

| Capability | Status | Notes |
|---|---|---|
| CSV-based historical simulation | ✅ Done | date/symbol/open/close format |
| Trigger + allocation logic | ⚠️ Partial | No daily cap; `SILVER_SYMBOL` uses old bare symbol, Silver rule never fires |
| NAV check | ❌ Not implemented | No historical NAV data |

---

## What is NOT yet implemented (Planned)

These are described in the project roadmap as future stages. No code exists for any of them.

| Feature | Stage | Notes |
|---|---|---|
| Static IP integration into live flow | Stage 2 (partial) | `ip_guard.py` exists but is not wired |
| Groww read-only connector | Stage 3 | No `groww_connector.py` exists |
| Instrument matching (Groww ↔ Angel) | Stage 4 | No `instrument_map.py` exists |
| Dynamic watchlist from real holdings | Stage 5 | No `watchlist.py` exists |
| Price provider abstraction / fallback | Stage 6 | No `prices.py` exists |
| Unified portfolio model | Stage 7 | No `portfolio.py` exists |
| Buy signals with weighted avg and P&L context | Stage 9 | Planned |
| Purchase/sale reconciliation from holdings | Stage 10 | Planned |
| Sell / profit-booking alerts | Stage 11 | Planned |
| CLI `--portfolio`, `--reconcile`, `--check` | Stage 12 | Planned |
| Daily summary with per-broker breakdown | Stage 12 | Planned |

---

## Known issues

| Issue | Severity | Location |
|---|---|---|
| CI `.env` uses `MODE=dry` which `config.py` now rejects | High | `.github/workflows/tests.yml` |
| `ip_guard.py` never called — live orders bypass IP check | High | `bot.py` (missing import) |
| `alerts.py` is orphaned — replaced by `notifier.py` | Low | `alerts.py` |
| `market_detector.update_history()` never called — accumulation always False | Low | `bot.py` |
| `backtest.py` uses old symbol name — Silver rule never fires | Medium | `backtest.py` |
| `NAVChecker.best_etf_to_buy()` defined but never called | Low | `nav_checker.py` |
