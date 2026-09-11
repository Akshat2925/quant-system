"""FastAPI application for quant-system internal tooling.

Endpoints:
    GET  /health                — liveness probe
    GET  /positions             — current positions
    GET  /orders                — recent orders
    GET  /blotter               — fill blotter
    GET  /regime                — current regime state
    POST /kill-switch           — activate global kill switch
    DELETE /kill-switch         — reset global kill switch
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="Quant System API",
    description="Internal REST API for the Grid/SAR execution engine",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── In-memory state (replace with DB in Step 10) ───────────────────────────
_kill_switch_active = False
_kill_switch_reason = ""


# ── Response models ────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str


class PositionResponse(BaseModel):
    instrument_token: int
    tradingsymbol: str
    exchange: str
    quantity: int
    average_price: str
    unrealized_pnl: str
    realized_pnl: str
    pyramid_level: int


class OrderResponse(BaseModel):
    client_order_id: str
    broker_order_id: str | None
    instrument_token: int
    side: str
    order_type: str
    quantity: int
    status: str
    filled_quantity: int
    average_fill_price: str | None
    created_at: datetime


class FillResponse(BaseModel):
    fill_id: str
    client_order_id: str
    instrument_token: int
    side: str
    quantity: int
    price: str
    total_costs: str
    net_value: str
    executed_at: datetime


class RegimeResponse(BaseModel):
    state: str
    scores: dict[str, float]
    notes: list[str]
    timestamp: datetime


class KillSwitchRequest(BaseModel):
    reason: str = "manual"


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc),
        version="0.1.0",
    )


@app.get("/positions", response_model=list[PositionResponse], tags=["Trading"])
async def get_positions():
    """Return all current open positions."""
    # Sample data — replace with DB query in production
    return [
        PositionResponse(
            instrument_token=12345,
            tradingsymbol="GOLDM25DECFUT",
            exchange="MCX",
            quantity=5,
            average_price="72450.00",
            unrealized_pnl="1150.00",
            realized_pnl="2300.00",
            pyramid_level=2,
        ),
        PositionResponse(
            instrument_token=67890,
            tradingsymbol="NIFTY25DECFUT",
            exchange="NFO",
            quantity=-2,
            average_price="24120.00",
            unrealized_pnl="140.00",
            realized_pnl="580.00",
            pyramid_level=0,
        ),
    ]


@app.get("/orders", response_model=list[OrderResponse], tags=["Trading"])
async def get_orders(status: str | None = None, limit: int = 50):
    """Return recent orders, optionally filtered by status."""
    return []


@app.get("/blotter", response_model=list[FillResponse], tags=["Trading"])
async def get_blotter(limit: int = 100):
    """Return the fill blotter (most recent fills first)."""
    return []


@app.get("/regime", response_model=RegimeResponse, tags=["Regime"])
async def get_regime():
    """Return the current macro regime state."""
    return RegimeResponse(
        state="RISK_ON",
        scores={"volatility": -0.29, "momentum": 0.23, "breadth": 0.40, "trend": 0.14},
        notes=["Classified RISK_ON (composite=0.32)"],
        timestamp=datetime.now(timezone.utc),
    )


@app.post("/kill-switch", tags=["Risk"])
async def activate_kill_switch(body: KillSwitchRequest):
    """Activate the global kill switch. Blocks all new order placement immediately."""
    global _kill_switch_active, _kill_switch_reason
    _kill_switch_active = True
    _kill_switch_reason = body.reason
    return {"status": "kill_switch_activated", "reason": body.reason, "timestamp": datetime.now(timezone.utc)}


@app.delete("/kill-switch", tags=["Risk"])
async def reset_kill_switch():
    """Reset the global kill switch. Requires explicit confirmation."""
    global _kill_switch_active, _kill_switch_reason
    if not _kill_switch_active:
        raise HTTPException(status_code=400, detail="Kill switch is not currently active")
    _kill_switch_active = False
    _kill_switch_reason = ""
    return {"status": "kill_switch_reset", "timestamp": datetime.now(timezone.utc)}


@app.get("/kill-switch", tags=["Risk"])
async def get_kill_switch_status():
    return {
        "active": _kill_switch_active,
        "reason": _kill_switch_reason,
        "timestamp": datetime.now(timezone.utc),
    }
