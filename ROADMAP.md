# Roadmap

Progress toward v3.0.0. Each stage is one logical unit of work.
See [docs/STATUS.md](docs/STATUS.md) for detailed per-feature status.

---

## v2.0.0 — Complete ✅

Multi-ETF alert and buying system. See [CHANGELOG.md](CHANGELOG.md).

---

## v3.0.0 — In Development 🔄

### Foundation (Stages 0–2)
- [x] Stage 0 — Audit, symbol fix (`-EQ` suffix, correct silver symbol)
- [x] Stage 1 — Operating mode system (`alert_only` / `dry_run` / `live`)
- [x] Stage 2 — Static IP guard integrated into live flow; LIMIT-only orders enforced

### Data Sources (Stages 3–8)
- [x] Stage 3 — Groww read-only connector (holdings source)
- [x] Stage 4 — Instrument matching: Groww holding → Angel symbol/token
- [x] Stage 5 — Dynamic watchlist built from live holdings (Groww + Angel + cache + yaml overrides)
- [x] Stage 6 — Price provider with fallback and staleness guard
- [x] Stage 7 — Unified portfolio model (qty, avg price, P&L per broker)
- [x] Stage 8 — Sync schedule, caches, graceful degradation on broker failure

### Signals and Alerts (Stages 9–12)
- [x] Stage 9 — BUY SIGNAL with full context (avg price, new avg after buy, P&L, portfolio weight)
- [x] Stage 10 — Purchase/sale reconciliation from holdings diff
- [x] Stage 11 — Sell / profit-booking alerts (never auto-sell)
- [x] Stage 12 — CLI tools: `--portfolio`, `--reconcile`, `--check`; Telegram `/portfolio`

### Documentation and Release (Stages 13–16)
- [x] Stage 13 — Groww order stub (NotImplementedError placeholder)
- [x] Stage 14 — Config and docs: all new keys, updated README
- [x] Stage 15 — Full test suite covering all new stages
- [x] Stage 16 — Final report and dry-run validation period

---

## v3.0.0 release criteria

- [ ] All 16 stages complete
- [ ] At least 5 trading days dry-run with no unexpected behavior
- [ ] All tests pass on CI
- [ ] `docs/STATUS.md` shows no open "Known issues"
- [ ] `scripts/pre_push_check.py` passes with no strategy values in tracked files
