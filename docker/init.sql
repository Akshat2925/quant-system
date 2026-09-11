-- TimescaleDB initialisation for quant-system
-- Run once on first container start.

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ── Instruments ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS instruments (
    instrument_token    BIGINT PRIMARY KEY,
    tradingsymbol       TEXT NOT NULL,
    exchange            TEXT NOT NULL,
    contract_type       TEXT NOT NULL,
    lot_size            INT NOT NULL,
    tick_size           NUMERIC NOT NULL,
    price_quotation_basis TEXT DEFAULT 'per_unit',
    expiry              DATE,
    underlying          TEXT,
    strike              NUMERIC,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ── Orders ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    client_order_id     TEXT PRIMARY KEY,
    broker_order_id     TEXT,
    instrument_token    BIGINT REFERENCES instruments(instrument_token),
    side                TEXT NOT NULL,
    order_type          TEXT NOT NULL,
    quantity            INT NOT NULL,
    limit_price         NUMERIC,
    trigger_price       NUMERIC,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    filled_quantity     INT DEFAULT 0,
    average_fill_price  NUMERIC,
    strategy_run_id     TEXT NOT NULL,
    tags                JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ── Fills (hypertable — time-series) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS fills (
    fill_id             TEXT NOT NULL,
    client_order_id     TEXT REFERENCES orders(client_order_id),
    instrument_token    BIGINT,
    side                TEXT NOT NULL,
    quantity            INT NOT NULL,
    price               NUMERIC NOT NULL,
    brokerage           NUMERIC DEFAULT 0,
    stt_ctt             NUMERIC DEFAULT 0,
    exchange_charges    NUMERIC DEFAULT 0,
    gst                 NUMERIC DEFAULT 0,
    stamp_duty          NUMERIC DEFAULT 0,
    slippage_vs_intent  NUMERIC,
    source              TEXT DEFAULT 'live',
    strategy_run_id     TEXT,
    executed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable('fills', 'executed_at', if_not_exists => TRUE);

-- ── Positions ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    id                  SERIAL PRIMARY KEY,
    instrument_token    BIGINT,
    strategy_run_id     TEXT,
    quantity            INT NOT NULL DEFAULT 0,
    average_price       NUMERIC DEFAULT 0,
    realized_pnl        NUMERIC DEFAULT 0,
    total_costs         NUMERIC DEFAULT 0,
    pyramid_level       INT DEFAULT 0,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ── Equity curve (hypertable) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS equity_curve (
    strategy_run_id     TEXT NOT NULL,
    instrument_token    BIGINT,
    mark_pnl            NUMERIC NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable('equity_curve', 'recorded_at', if_not_exists => TRUE);

-- ── Regime snapshots ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS regime_snapshots (
    id                  SERIAL PRIMARY KEY,
    state               TEXT NOT NULL,
    scores              JSONB DEFAULT '{}',
    proxies             JSONB DEFAULT '[]',
    notes               TEXT[],
    recorded_at         TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_fills_instrument ON fills(instrument_token, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_equity_run ON equity_curve(strategy_run_id, recorded_at DESC);
