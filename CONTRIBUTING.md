# Contributing to quant-system

## Ground rules

1. **Every strategy change ships with a regression test.** The `RegressionGuard` agent will block your push if it doesn't.
2. **Money is `Decimal`, never `float`.** No exceptions in `core/`, `backtest/`, or `execution/`.
3. **One implementation per indicator.** Import from `quant_system.indicators`, never reimplement ATR/RSI elsewhere.
4. **Backtest must reconcile to live.** If you change `Position.apply_fill`, update the hand-computed test expectations in `test_position.py`.

## Setup

```bash
git clone https://github.com/Akshat2925/quant-system.git
cd quant-system
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest tests/unit tests/regression -v
```

## Branch naming

```
feat/short-description
fix/short-description
refactor/short-description
```

## Commit style

```
feat: add CMF indicator with unit tests
fix: SAR kill switch not checked before emit
refactor: extract SlippageModel from backtest engine
test: add regression for grid position cap off-by-one
```

## Pull request checklist

- [ ] Tests pass: `pytest tests/unit tests/regression`
- [ ] Lint passes: `ruff check src/ tests/`
- [ ] New strategy/indicator code has unit tests
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] No float in P&L paths
