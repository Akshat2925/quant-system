# Changelog

All notable changes to quant-system are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

## [0.1.0] — 2026-09-11

### Added
- **Core models** (`core/`): Instrument, Order, Fill, Position, idempotency
  - Order lifecycle state machine with `_ALLOWED_TRANSITIONS` table
  - `Position.apply_fill` — canonical P&L, handles open/pyramid/close/reversal
  - Deterministic `client_order_id` generation for idempotent placement
  - `OrderStatus.UNKNOWN` as first-class safety state
- **Indicators** (`indicators/`): 16 indicators, single implementation each
  - Volatility: ATR (Wilder), Bollinger Bands, Historical Volatility
  - Trend: SMA, EMA, MACD, ADX, Supertrend
  - Momentum: RSI, Stochastic, ROC, Williams %R
  - Volume: OBV, VWAP, VolumeSMA, CMF
- **Macro Regime Engine** (`regime/`): VIX + RSI + A/D → RISK_ON/OFF/HIGH_VOL/CIRCUIT_HALT
- **Strategy engines** (`strategies/`):
  - Grid: ATR-spaced, pyramiding, kill switch, position cap
  - Stop-and-Reverse: trailing ATR stop, SL_M reversal order
- **Backtest harness** (`backtest/`):
  - Bar-accurate fills, no lookahead guarantee
  - Slippage model (fixed ticks + proportional bps)
  - Indian cost model (brokerage, STT/CTT, exchange charges, GST, stamp duty)
  - Walk-forward splits
  - Sharpe ratio and max drawdown
- **Broker integration** (`broker/`):
  - KiteAdapter: REST (tenacity retries, token-bucket rate limiter) + WebSocket (asyncio queue, back-pressure)
  - PaperBroker: in-memory paper trading for tests
- **Execution engine** (`execution/`): async, idempotent, crash recovery
- **Risk module** (`risk/`): CircuitBreaker, RiskManager (daily loss, drawdown, order rate, global kill switch)
- **Observability** (`observability/`): structlog JSON logging, trade blotter, Telegram/webhook alerting
- **SDLC agents** (`agents/`): RegressionGuard, BlotterReconciler, CoverageEnforcer
- **Dashboard** (`dashboard/`): Streamlit live P&L, positions, blotter, regime, circuit breakers
- **API** (`api/`): FastAPI endpoints for positions, orders, blotter, regime, kill switch
- **Docker**: docker-compose with TimescaleDB, Redis, Grafana, API, Dashboard
- **CI**: GitHub Actions — test matrix (Python 3.11, 3.12), coverage, ruff lint
- **Tests**: 94 unit + regression tests, 0 failures

[0.1.0]: https://github.com/Akshat2925/quant-system/releases/tag/v0.1.0
