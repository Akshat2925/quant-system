# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

---

## [Unreleased] — 3.0.0-alpha in development

### In Progress
- Stage 2: Static IP guard integration, LIMIT-only order enforcement
- Step documentation: README overhaul, ARCHITECTURE, GLOSSARY, strategy privacy

---

## [3.0.0-alpha.1] — 2026-09-30

### Added
- `version.py` — single source of truth for version string
- `docs/STATUS.md` — verified inventory of what is done vs planned
- `notifier.py` — full Telegram notification layer replacing `alerts.py`
  - 17 event types with `[ALERT ONLY]` / `[DRY RUN]` / `[LIVE]` mode tags
  - Non-blocking background send queue with retry and 429 handling
  - Per-day dedupe persisted across restarts (separate file per mode)
  - Dual chat-ID support (`TELEGRAM_CHAT_ID_2`)
  - `/status` command (read-only, responds only to configured chat ID)
  - Auto-reply to any other incoming message
  - `--test-telegram` CLI flag: sends sample of every event type
  - `--simulate-trigger SYMBOL PCT` CLI flag (alert/dry modes only)
- `ip_guard.py` — static IP check utilities (not yet integrated into live flow)
- `START_BOT_ALERT.bat` — desktop launcher for alert-only mode
- `tests/test_notifier.py` — 22 tests (token masking, dedupe, retries, mode tags)
- `tests/test_mode.py` — 12 tests (mode system, CLI flags, config validation)
- `tests/test_bot_guards.py` — 6 tests (IST timezone, holiday guard, open-price logic)

### Changed
- **Operating mode system**: `MODE` now accepts `alert_only` (default), `dry_run`, `live`
  - Old value `dry` is now rejected with a clear error
  - `alert_only` makes zero order API calls; sends BUY SIGNAL to Telegram instead
  - `live` requires interactive `YES` confirmation or `--confirm-live` flag
- **ETF symbols**: all five ETFs now use the correct Angel One `-EQ` suffix
  - `ICICISILVER` → `SILVERIETF-EQ` (was wrong name entirely)
  - `CPSEETF` → `CPSEETF-EQ`, `METALIETF` → `METALIETF-EQ` (missing suffix — caused "no price" bug)
  - Tokens verified against `OpenAPIScripMaster.json`
- `START_BOT_LIVE.bat` — now fails loudly if `MODE` in `.env` is not `live`
- `start_bot_task.bat` — reads `MODE` from `.env`, launches correct mode

### Fixed
- CPSEETF and METALIETF had no price data due to missing `-EQ` suffix
- ICICISILVER was a non-existent symbol; correct symbol is `SILVERIETF-EQ`
- `_parse_simulate_trigger` was unreachable dead code (merged inside another function); extracted correctly
- CI workflow `.env` used `MODE=dry` which is now rejected by `config.py`

---

## [2.0.0] — 2026-09-17

### Added
- Multi-ETF support (5 ETFs across gold, silver, defence, PSU, metal)
- Proportional budget allocation based on fall magnitude
- Monthly budget cap with configurable daily spending limit
- Full SEBI-style NAV premium check (advisory and strict modes)
- NAV staleness guard (> 3 days old treated as unavailable)
- Special fixed-allocation rule for one ETF category
- Atomic JSON state writes (crash-safe)
- Per-symbol once-per-day guard (survives restart)
- Separate state files for dry vs live mode
- Order fill verification: only filled orders count against budget
- Corrupt state detection, backup, and live-order block
- Month rollover: budget resets automatically
- Rounding carry-back: unspent fraction passed to next ETF
- Daily spending cap (`DAILY_CAP_PCT`)
- Auto re-login on Angel One session expiry
- IST timezone via `zoneinfo` throughout
- NSE holiday guard
- EOD auto-exit at 15:35 IST with daily summary
- Heartbeat alert at 9:20 AM
- `market_detector.py`: Nifty 50 market-wide fall alert (advisory only)
- Streamlit dashboard with live/dry mode selector
- Full pytest suite (82 tests, all mocked)
- CI via GitHub Actions

### Changed
- Replaced single-ETF NIFTYBEES logic with multi-ETF engine
- Capital-percentage tranches replaced by proportional allocation
- `open_prices.json` removed; day-open now from `ltpData()` on every cycle
- `get_funds()` returns `None` on failure (was `0.0`)

---

## [1.0.0] — 2026-09-17 (archived)

Original single-ETF bot for NIFTYBEES on Angel One.
Tranche-based buying at fixed percentage drops.
Source preserved in the `main` branch.

---

[Unreleased]: https://github.com/Akshat2925/quant-system/compare/v3.0.0-alpha.1...HEAD
[3.0.0-alpha.1]: https://github.com/Akshat2925/quant-system/compare/v2.0.0...v3.0.0-alpha.1
[2.0.0]: https://github.com/Akshat2925/quant-system/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/Akshat2925/quant-system/tree/main
