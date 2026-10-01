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
- [ ] Stage 2 — Static IP guard integrated into live flow; LIMIT-only orders enforced

### Data Sources (Stages 3–8)
- [ ] Stage 3 — Groww read-only connector (holdings source)
- [ ] Stage 4 — Instrument matching: Groww holding → Angel symbol/token
- [ ] Stage 5 — Dynamic watchlist built from live holdings (Groww + Angel + cache + yaml overrides)
- [ ] Stage 6 — Price provider with fallback and staleness guard
- [ ] Stage 7 — Unified portfolio model (qty, avg price, P&L per broker)
- [ ] Stage 8 — Sync schedule, caches, graceful degradation on broker failure

### Signals and Alerts (Stages 9–12)
- [ ] Stage 9 — BUY SIGNAL with full context (avg price, new avg after buy, P&L, portfolio weight)
- [ ] Stage 10 — Purchase/sale reconciliation from holdings diff
- [ ] Stage 11 — Sell / profit-booking alerts (never auto-sell)
- [ ] Stage 12 — CLI tools: `--portfolio`, `--reconcile`, `--check`; Telegram `/portfolio`

### Documentation and Release (Stages 13–16)
- [ ] Stage 13 — Groww order stub (NotImplementedError placeholder)
- [ ] Stage 14 — Config and docs: all new keys, updated README
- [ ] Stage 15 — Full test suite covering all new stages
- [ ] Stage 16 — Final report and dry-run validation period

---

## v3.0.0 release criteria

- [ ] All 16 stages complete
- [ ] At least 5 trading days dry-run with no unexpected behavior
- [ ] All tests pass on CI
- [ ] `docs/STATUS.md` shows no open "Known issues"
- [ ] `scripts/pre_push_check.py` passes with no strategy values in tracked files
