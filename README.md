# quant-system

[![CI](https://github.com/Akshat2925/quant-system/actions/workflows/ci.yml/badge.svg)](https://github.com/Akshat2925/quant-system/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-94%20passing-brightgreen.svg)](https://github.com/Akshat2925/quant-system/actions)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**Grid / Stop-and-Reverse execution system for MCX/NSE, built on Zerodha Kite Connect.**

> Numerical discipline: every P&L figure is computed in `Decimal` — never `float`.
> Backtest reconciles to live by construction, not by hope.

## Status

- [x] Step 1 — Architecture
- [x] Step 2 — Repository structure
- [x] Step 3 — Core trading models (`core/`: Instrument, Order, Fill, Position, idempotency) — 33 unit tests, 97% coverage
- [x] Step 4 — Indicators (ATR, EMA, SMA, MACD, ADX, Supertrend, RSI, Stochastic, ROC, Williams %R, OBV, VWAP, VolumeSMA, CMF, Bollinger Bands, HV) — single implementation each, 28 unit tests
- [x] Step 5 — Backtester (bar-accurate fills, slippage + cost model, walk-forward, no lookahead, reconciles to live P&L)
- [x] Step 6 — Trading engine (Grid + Stop-and-Reverse strategies, Macro Regime Engine with circuit breakers)
- [x] Step 7 — Broker / WebSocket integration (KiteAdapter: REST + WebSocket, token bucket rate limiter, tenacity retries, PaperBroker for tests)
- [x] Step 8 — Risk: circuit breakers, global kill switch, daily loss limit, order rate limit
- [x] Step 9 — Tests: 94 tests passing (unit + regression); regression tests ship with every strategy change
- [ ] Step 10 — Docker / DB / monitoring (TimescaleDB, Redis, Grafana — structure ready)
- [ ] Step 11 — Final audit + README

**Total: 94 tests, 0 failures.**

## Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -e ".[dev]"
pytest tests/unit tests/regression -v --cov=quant_system
```

## Architecture

```
src/quant_system/
├── core/           # Order, Fill, Position, Instrument, idempotency — no I/O, fully tested
│   ├── enums.py    # Exchange, Side, OrderType, OrderStatus, RegimeState
│   ├── instrument.py
│   ├── order.py    # State machine with _ALLOWED_TRANSITIONS table
│   ├── fill.py     # Immutable, itemized costs (brokerage/STT/GST/etc.)
│   ├── position.py # Canonical P&L: weighted-avg, pyramiding, reversal
│   └── idempotency.py  # Deterministic client_order_id
│
├── indicators/     # Single implementation each — import from here, never reimplement
│   ├── volatility.py  # ATR (Wilder), Bollinger Bands, Historical Volatility
│   ├── trend.py       # SMA, EMA, MACD, ADX, Supertrend
│   ├── momentum.py    # RSI, Stochastic, ROC, Williams %R
│   └── volume.py      # OBV, VWAP, VolumeSMA, CMF
│
├── regime/         # Macro Regime Engine
│   └── engine.py   # MacroProxy → RegimeState + param overrides + circuit breakers
│
├── strategies/     # Strategy → OrderIntent only; never touches broker
│   ├── base.py     # Bar, OrderIntent, Strategy ABC
│   ├── grid/       # ATR-spaced grid, pyramiding, kill switch, position cap
│   └── stop_and_reverse/  # Trailing ATR stop, reversal, kill switch
│
├── backtest/       # Uses Position.apply_fill — same P&L path as live
│   ├── engine.py   # Bar-accurate fills, slippage, cost model, Sharpe, drawdown
│   └── walk_forward.py  # Non-overlapping train/test splits, no lookahead
│
├── broker/
│   ├── base.py     # AbstractBroker interface
│   ├── kite/       # KiteAdapter: REST (tenacity retries) + WebSocket (asyncio queue)
│   └── backtest/   # PaperBroker: in-memory, same interface as live
│
├── execution/      # Async engine: intent → order → broker → fill → position
│   └── engine.py   # Idempotent placement, reconciliation on restart, back-pressure
│
├── risk/           # Circuit breakers and kill switches
│   └── circuit_breaker.py  # CircuitBreaker, RiskManager (daily loss, drawdown, rate)
│
├── observability/
│   ├── logging.py  # structlog setup (JSON in prod, console in dev)
│   ├── blotter.py  # Append-only fill log; to_dataframe() / to_csv() for reconciliation
│   └── alerting.py # Telegram + webhook alerts; CRITICAL fires for kill switch events
│
└── agents/         # SDLC automation
    └── sdlc.py     # RegressionGuard, BlotterReconciler, CoverageEnforcer
```

## Design notes

### Money is `Decimal`, never `float`
Paisa-level reconciliation requires exact arithmetic. Every price/P&L field
in `core/` is `Decimal`. Float is acceptable in indicator math (ATR, RSI etc.)
since these are signals, not accounting.

### Single P&L source of truth
`Position.apply_fill` is the canonical implementation. The backtester imports
and calls it — it does NOT reimplement the math. "Backtest reconciles to live"
is structurally enforced, not just a hope.

### Order state machine
`Order.transition_to()` enforces `_ALLOWED_TRANSITIONS`. `OrderStatus.UNKNOWN`
is a first-class state: any instrument with an UNKNOWN order is blocked for
new orders until reconciliation resolves it.

### Idempotent order placement
`make_client_order_id(strategy_run_id, instrument_token, intent_sequence)` is
deterministic. Retrying a timed-out placement call reuses the same ID; the
broker/reconciliation layer recognises it as a duplicate.

### Strategy → Execution separation
Strategies produce `OrderIntent` objects. The `ExecutionEngine` converts intents
into `Order` objects and routes them through the broker. The same strategy code
runs in backtest (PaperBroker) and live (KiteAdapter) without modification.

### Backtest no-lookahead guarantee
Strategy sees bar[i] → emits intents → intents enter pending queue → eligible
to fill on bar[i+1] and later. Market orders fill at bar[i+1].open with
slippage. Limit/stop orders carry forward until their price level is hit.

### Regime-driven parameter overrides
The `RegimeEngine` evaluates macro proxies (India VIX, market RSI, A/D ratio)
and returns a `RegimeState`. The execution engine calls
`engine.apply_overrides(base_params, state)` before passing params to strategies.
`CIRCUIT_HALT` sets `position_cap_lots=0`, which both strategies check before
emitting any intent.

### Concurrency model
- `KiteAdapter.stream_ticks()`: KiteTicker runs in a daemon thread, pushes ticks
  to an `asyncio.Queue`. Back-pressure: if the queue is full, ticks are dropped
  with a warning (slow consumers don't block the WebSocket thread).
- `ExecutionEngine`: single asyncio task serialises order placement; intents are
  submitted via a non-blocking queue from the strategy loop.

### SDLC agents
- `RegressionGuard`: pre-push check that every changed strategy file has a
  regression test containing at least one `test_` function.
- `BlotterReconciler`: compares two blotters paise-by-paise; fails CI if any
  fill's net value differs beyond `tolerance_paise`.
- `CoverageEnforcer`: runs pytest-cov and fails CI below `min_coverage` threshold.
