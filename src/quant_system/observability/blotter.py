"""Trade blotter: immutable append-only log of all fills and order events.

The blotter is the reconciliation source — it must match the broker's
trade log and the desk's spreadsheet to the paisa.

Blotter records are written to:
1. In-memory list (for intraday dashboard / API)
2. Structured log (for long-term storage and grep-ability)
3. Optionally: a database table (Step 10)

Design: all methods are synchronous and cheap. Blotter writes happen in the
hot path after every fill — they must not block order processing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from quant_system.core.enums import Side
from quant_system.core.fill import Fill

logger = logging.getLogger(__name__)


@dataclass
class BlotterEntry:
    """One blotter record per fill event."""

    fill_id: str
    client_order_id: str
    broker_order_id: str | None
    instrument_token: int
    tradingsymbol: str
    exchange: str
    side: Side
    quantity: int
    price: Decimal
    gross_value: Decimal
    brokerage: Decimal
    stt_ctt: Decimal
    exchange_charges: Decimal
    gst: Decimal
    stamp_duty: Decimal
    total_costs: Decimal
    net_value: Decimal
    slippage_vs_intent: Decimal | None
    strategy_run_id: str
    tags: dict[str, str]
    executed_at: datetime
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        d = asdict(self)
        # Convert Decimal and datetime to strings for JSON serialisation
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = str(v)
            elif isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, Side):
                d[k] = v.value
        return d


class Blotter:
    """Append-only trade blotter.

    Usage:
        blotter = Blotter(run_id="run-abc123")
        blotter.record_fill(fill, broker_order_id, tradingsymbol, exchange, strategy_run_id, tags)
        df = blotter.to_dataframe()   # pandas DataFrame for analysis
    """

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._entries: list[BlotterEntry] = []

    def record_fill(
        self,
        fill: Fill,
        broker_order_id: str | None,
        tradingsymbol: str,
        exchange: str,
        strategy_run_id: str,
        tags: dict[str, str] | None = None,
    ) -> BlotterEntry:
        entry = BlotterEntry(
            fill_id=fill.fill_id,
            client_order_id=fill.client_order_id,
            broker_order_id=broker_order_id,
            instrument_token=fill.instrument_token,
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            gross_value=fill.gross_value,
            brokerage=fill.brokerage,
            stt_ctt=fill.stt_ctt,
            exchange_charges=fill.exchange_charges,
            gst=fill.gst,
            stamp_duty=fill.stamp_duty,
            total_costs=fill.total_costs,
            net_value=fill.net_value,
            slippage_vs_intent=fill.slippage_vs_intent,
            strategy_run_id=strategy_run_id,
            tags=tags or {},
            executed_at=fill.executed_at,
        )
        self._entries.append(entry)
        logger.info("blotter_fill", extra=entry.to_dict())
        return entry

    @property
    def entries(self) -> list[BlotterEntry]:
        return list(self._entries)

    def total_realized_pnl(self, entries: Sequence[BlotterEntry] | None = None) -> Decimal:
        """Sum realized P&L across all blotter entries (buy/sell pairs).
        This is a cross-check against Position.realized_pnl — they must match."""
        rows = entries if entries is not None else self._entries
        total = Decimal(0)
        for e in rows:
            if e.side == Side.SELL:
                total += e.net_value
            else:
                total -= e.net_value
        return total

    def total_costs(self, entries: Sequence[BlotterEntry] | None = None) -> Decimal:
        rows = entries if entries is not None else self._entries
        return sum((e.total_costs for e in rows), Decimal(0))

    def to_dataframe(self):  # type: ignore[return]
        """Convert blotter to pandas DataFrame. Import pandas lazily."""
        try:
            import pandas as pd
            return pd.DataFrame([e.to_dict() for e in self._entries])
        except ImportError:
            raise ImportError("pandas is required for blotter.to_dataframe()")

    def to_csv(self, path: str) -> None:
        self.to_dataframe().to_csv(path, index=False)
        logger.info("blotter_exported_csv", extra={"path": path, "rows": len(self._entries)})
