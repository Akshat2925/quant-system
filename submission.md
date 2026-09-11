# Quant Developer Assignment Submission

**Name:** Akshat Nautiyal
**Roll No:** 2310992575
**GitHub Repository:** https://github.com/Akshat2925/quant-system

---

## What Was Built

A production-grade Grid / Stop-and-Reverse execution system for MCX/NSE markets, built on Zerodha Kite Connect. The system is fully implemented in Python 3.11+ with 94 passing tests.

---

## System Components

### 1. Core Trading Models (`core/`)
- **Instrument**: Exchange-agnostic contract model supporting MCX and NSE F&O. Handles MCX price quotation basis (e.g. Gold quoted per 10g).
- **Order**: Full lifecycle state machine with enforced transitions. `OrderStatus.UNKNOWN` is a first-class state that blocks new orders until reconciliation resolves it.
- **Fill**: Immutable execution record with itemized costs — brokerage, STT/CTT, exchange charges, GST, stamp duty.
- **Position**: Canonical P&L engine using weighted-average cost. Handles opening, pyramiding, partial close, full close, and reversal in one method. Both live engine and backtester call this same method — never reimplemented.
- **Idempotency**: Deterministic `client_order_id` generation from `(strategy_run_id, instrument_token, intent_sequence)`. Makes retrying placement calls safe against double-fills.

### 2. Technical Indicators (`indicators/`)
Single implementation of each indicator — imported everywhere, never duplicated:
- **Volatility**: ATR (Wilder smoothing), Bollinger Bands, Historical Volatility
- **Trend**: SMA, EMA, MACD, ADX, Supertrend
- **Momentum**: RSI (Wilder), Stochastic %K/%D, ROC, Williams %R
- **Volume**: OBV, VWAP, Volume SMA, CMF

### 3. Macro Regime Engine (`regime/`)
Ingests macro proxies (India VIX, market RSI, Advance/Decline ratio, ADX) and classifies market state into: `RISK_ON`, `RISK_OFF`, `NEUTRAL`, `HIGH_VOL`, `CIRCUIT_HALT`. Applies config-driven parameter overrides on top of base strategy params. `CIRCUIT_HALT` forces `position_cap_lots=0` across all strategies.

### 4. Trading Strategies (`strategies/`)
- **Grid Strategy**: ATR-spaced dynamic grid. Pyramids into the trend up to `max_pyramid_levels`. Grid floats with volatility (recomputed from ATR each bar). Kill switch and position cap enforced before any intent is emitted. Strategies produce `OrderIntent` objects only — never touch the broker directly.
- **Stop-and-Reverse Strategy**: Always in the market. Trailing ATR stop. Reversal order is `SL_M` type with trigger price — atomic flip at the broker level. Kill switch and zero position-cap check enforced.

### 5. Backtest Engine (`backtest/`)
- Bar-accurate fill simulation: market orders fill at bar[i+1].open, limit/stop orders carry forward until their price level is hit
- No lookahead enforced structurally: strategy sees bar[i], intents queue, fills execute on bar[i+1]+
- Slippage model: fixed ticks + proportional bps
- Cost model: reproduces full Indian F&O cost structure (brokerage, STT, exchange charges, GST, stamp duty)
- Walk-forward splits: non-overlapping train/test windows
- P&L from backtest matches live P&L by construction (both call `Position.apply_fill`)

### 6. Broker Integration (`broker/`)
- **KiteAdapter**: REST via `kiteconnect` library wrapped in `asyncio.to_thread`. Tenacity retries with exponential backoff (3 attempts, 1-10s). Token-bucket rate limiter (3 req/s sustained, 10 burst).
- **WebSocket**: KiteTicker in daemon thread, ticks pushed to `asyncio.Queue`. Back-pressure: queue-full drops oldest tick with warning — slow consumers never block the WebSocket thread.
- **PaperBroker**: In-memory broker implementing the same `AbstractBroker` interface — execution engine code is identical in backtest and live.

### 7. Risk Management (`risk/`)
- `CircuitBreaker`: Threshold-based, optional auto-reset cooldown, structured event log
- `RiskManager`: Daily loss limit, max drawdown limit, order rate limit (per minute), global kill switch
- All checks are synchronous and run in the hot path before every order placement

### 8. Execution Engine (`execution/`)
- Async order lifecycle manager running in an `asyncio` event loop
- Idempotent placement: checks `client_order_id` before placing to prevent duplicates
- Crash recovery: queries broker for open orders on startup, sets unresolvable orders to `UNKNOWN`
- Intent queue with back-pressure: drops intents when queue is full with structured warning

### 9. Observability (`observability/`)
- **Structured logging**: `structlog` with JSON output in production, console in development
- **Blotter**: Append-only fill log with itemized costs. Exports to pandas DataFrame or CSV for reconciliation against desk spreadsheets
- **Alerting**: Telegram and webhook channels. CRITICAL alerts fire for kill switch events, circuit breaker trips, and large drawdowns

### 10. SDLC Agents (`agents/`)
- **RegressionGuard**: AST-based pre-push check that every changed strategy file has a regression test with at least one `test_` function
- **BlotterReconciler**: Paise-level comparison of two blotters (e.g. backtest vs live). Fails CI if any fill's net value differs beyond `tolerance_paise`
- **CoverageEnforcer**: Runs pytest-cov and fails CI below `min_coverage` threshold

---

## Test Results

```
94 passed in 1.15s
```

- 33 unit tests: core models (Order, Fill, Position, Instrument, idempotency)
- 28 unit tests: all 16 indicators
- 10 unit tests: backtesting engine and walk-forward
- 10 unit tests: regime engine
- 9 unit tests: risk / circuit breakers
- 8 regression tests: Grid and SAR strategy correctness

---

## Key Design Decisions

**Money is always `Decimal`** — paisa-level reconciliation does not tolerate float drift. Every price, P&L, and cost field in `core/` uses `Decimal`.

**Single P&L source of truth** — `Position.apply_fill` is the only P&L implementation. The backtester imports and calls it. "Backtest reconciles to live" is structurally enforced, not hoped for.

**Order state machine** — `UNKNOWN` is a first-class safety state. Any instrument with an `UNKNOWN` order is blocked for new orders until reconciliation resolves it.

**Strategy/execution separation** — Strategies produce `OrderIntent` objects. The execution engine routes them to the broker. The same strategy code runs unmodified in backtest and live.

**Idempotent orders** — Deterministic `client_order_id` makes retrying timed-out placement calls safe. The broker recognises duplicates and does not double-fill.

---

## Repository Structure

```
src/quant_system/
├── core/           # Order, Fill, Position, Instrument, idempotency
├── indicators/     # ATR, RSI, MACD, Supertrend, VWAP, CMF, and 10 more
├── regime/         # Macro Regime Engine
├── strategies/     # Grid + Stop-and-Reverse
├── backtest/       # Backtesting engine + walk-forward
├── broker/         # KiteAdapter (live) + PaperBroker (test)
├── execution/      # Async execution engine
├── risk/           # Circuit breakers + kill switches
├── observability/  # Logging, blotter, alerting
└── agents/         # SDLC automation
```

**GitHub:** https://github.com/Akshat2925/quant-system
