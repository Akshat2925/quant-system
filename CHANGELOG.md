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
- **Full notification system** (`notifier.py`) replacing `alerts.py`
- `ip_guard.py` — static IP check, integrated into live mode
- `groww_connector.py` — read-only Groww holdings connector with auto token refresh
- `instrument_map.py` — Groww symbol → Angel token mapping with cache and overrides
- `watchlist.py` — dynamic watchlist from Groww + Angel + cache + yaml overrides
- `prices.py` — price provider with Angel primary, Groww fallback, cross-check
- `portfolio.py` — unified portfolio model (qty, avg price, P&L per broker)
- `sync.py` — sync scheduler for periodic holdings refresh
- `signals.py` — BUY SIGNAL with full context (avg, new avg, P&L, weight, funds)
- `reconcile.py` — purchase/sale detection from holdings diff
- `profit_alerts.py` — sell/profit-booking alerts at configurable gain levels
- `cli_tools.py` — `--check`, `--portfolio`, `--reconcile` CLI commands
- `instrument_overrides.example.yaml`, `watchlist.example.yaml` — example configs
- **204 tests** across 14 test files (all mocked, no real credentials)

### Changed
- **Operating mode system**: `MODE` now accepts `alert_only` (default), `dry_run`, `live`
- **ETF symbols**: all five ETFs now use correct Angel `-EQ` suffix
  - `ICICISILVER` → `SILVERIETF-EQ` (wrong name entirely — correct is SILVERIETF)
  - `CPSEETF` → `CPSEETF-EQ`, `METALIETF` → `METALIETF-EQ` (missing suffix = no price)
- `connector.py`: LIMIT-only orders, static IP rejection detection
- `START_BOT_LIVE.bat`: fails loud if MODE ≠ live in .env
- `start_bot_task.bat`: reads MODE from .env automatically
- CI: fixed `MODE=dry` → `MODE=alert_only`, added all dependencies

### Fixed
- CPSEETF and METALIETF had no price — missing `-EQ` suffix
- `notify_error` rate-limit used `time.monotonic()` with `0` default causing false rate-limit
- `_parse_simulate_trigger` was dead code (merged inside another function)
- Groww token expires daily at 6 AM — auto-refresh implemented

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
