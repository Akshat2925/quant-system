"""Backtest engine.

Bar-accurate fill simulation with slippage and cost modelling.
Uses Position.apply_fill — the exact same code as the live engine — so
backtest P&L reconciles to live P&L by construction.

Key design decisions:
- NO lookahead: strategies receive bars up to and including bar[i]; fill
  simulation uses bar[i+1] open (next bar open) for market orders, and
  checks bar[i+1] high/low for limit/stop orders.
- Slippage model: configurable fixed (ticks) + proportional (bps).
- Cost model: reproduces the itemized Fill structure (brokerage, STT/CTT,
  exchange charges, GST, stamp duty) so blotter output matches live fills.
- Walk-forward supported: split data with `WalkForwardSplitter`, run engine
  on in-sample, evaluate on out-of-sample.

The backtest produces a `BacktestResult` with per-trade blotter, equity curve,
and summary stats. Stats are reproducible — deterministic seed, no random fills.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from quant_system.core.enums import OrderType, Side
from quant_system.core.fill import Fill
from quant_system.core.instrument import Instrument
from quant_system.core.position import Position
from quant_system.strategies.base import Bar, OrderIntent, Strategy

logger = logging.getLogger(__name__)


@dataclass
class SlippageModel:
    """Slippage applied at fill time.

    fixed_ticks: always add this many ticks of adverse slippage.
    proportional_bps: add this fraction of price as adverse slippage.
    """

    fixed_ticks: int = 1
    proportional_bps: float = 0.0  # 1 bps = 0.0001

    def apply(self, price: Decimal, side: Side, tick_size: Decimal) -> Decimal:
        adverse = -1 if side == Side.BUY else 1
        slipped = price - adverse * self.fixed_ticks * tick_size
        if self.proportional_bps > 0:
            slipped -= adverse * price * Decimal(str(self.proportional_bps / 10000))
        return slipped


@dataclass
class CostModel:
    """Reproduces Indian equity/derivative cost structure for backtest fills.

    Rates approximate Zerodha Kite schedule for F&O (check zerodha.com/charges
    for current rates — these are indicative).
    """

    brokerage_per_lot: Decimal = Decimal("20")       # flat Rs 20 per order
    stt_ctt_rate: Decimal = Decimal("0.0001")        # 0.01% on sell side (F&O)
    exchange_charge_rate: Decimal = Decimal("0.0002")
    gst_rate: Decimal = Decimal("0.18")              # 18% on brokerage + exchange
    stamp_duty_rate: Decimal = Decimal("0.00003")    # 0.003% on buy side

    def compute(
        self,
        side: Side,
        price: Decimal,
        quantity: int,
        lot_size: int,
    ) -> dict[str, Decimal]:
        gross = price * quantity * lot_size
        brokerage = self.brokerage_per_lot  # flat per order
        exchange = gross * self.exchange_charge_rate
        stt_ctt = gross * self.stt_ctt_rate if side == Side.SELL else Decimal(0)
        gst = (brokerage + exchange) * self.gst_rate
        stamp = gross * self.stamp_duty_rate if side == Side.BUY else Decimal(0)
        return {
            "brokerage": brokerage.quantize(Decimal("0.01")),
            "stt_ctt": stt_ctt.quantize(Decimal("0.01")),
            "exchange_charges": exchange.quantize(Decimal("0.01")),
            "gst": gst.quantize(Decimal("0.01")),
            "stamp_duty": stamp.quantize(Decimal("0.01")),
        }


@dataclass
class TradeRecord:
    """One completed round-trip trade for blotter output."""

    instrument_token: int
    side: Side
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    total_costs: Decimal
    net_pnl: Decimal
    entry_time: datetime
    exit_time: datetime
    strategy_tags: dict[str, str] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Full output of a backtest run."""

    strategy_name: str
    instrument: Instrument
    bars_processed: int
    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    final_position: Position | None = None
    total_realized_pnl: Decimal = Decimal(0)
    total_costs: Decimal = Decimal(0)
    net_pnl: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal(0)
    sharpe_ratio: float | None = None
    num_trades: int = 0

    def summary(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "instrument": self.instrument.tradingsymbol,
            "bars": self.bars_processed,
            "num_trades": self.num_trades,
            "total_realized_pnl": str(self.total_realized_pnl),
            "total_costs": str(self.total_costs),
            "net_pnl": str(self.net_pnl),
            "max_drawdown": str(self.max_drawdown),
            "sharpe_ratio": self.sharpe_ratio,
        }


class BacktestEngine:
    """Runs a strategy over a sequence of bars, simulating fills.

    Usage:
        engine = BacktestEngine(slippage=SlippageModel(), costs=CostModel())
        result = engine.run(strategy, instrument, bars, strategy_params)
    """

    def __init__(
        self,
        slippage: SlippageModel | None = None,
        costs: CostModel | None = None,
        run_id: str | None = None,
    ) -> None:
        self._slippage = slippage or SlippageModel()
        self._costs = costs or CostModel()
        self._run_id = run_id or str(uuid.uuid4())

    def run(
        self,
        strategy: Strategy,
        instrument: Instrument,
        bars: Sequence[Bar],
        strategy_params: dict,
    ) -> BacktestResult:
        """Execute the backtest. bars must be in chronological order."""
        strategy.reset()
        position = Position(instrument_token=instrument.instrument_token)
        result = BacktestResult(
            strategy_name=strategy.name,
            instrument=instrument,
            bars_processed=0,
        )
        fills: list[Fill] = []
        equity: list[tuple[datetime, Decimal]] = []
        intent_seq = 0
        peak_equity = Decimal(0)

        bars_list = list(bars)
        n = len(bars_list)

        # pending_intents: emitted on bar[i], eligible to fill on bar[i+1] onwards
        # This enforces no-lookahead: strategy sees bar[i], fills happen on bar[i+1]+
        pending_intents: list[OrderIntent] = []
        # intents_this_bar: newly emitted on bar[i], NOT eligible to fill on bar[i]
        new_intents_buffer: list[OrderIntent] = []

        for i, bar in enumerate(bars_list):
            # Move last bar's new intents into pending (now eligible to fill)
            pending_intents.extend(new_intents_buffer)
            new_intents_buffer = []

            # Attempt to fill pending intents on this bar (bar[i+1] relative to emission)
            still_pending: list[OrderIntent] = []
            for intent in pending_intents:
                fill = self._simulate_fill(intent, bar, instrument)
                if fill is not None:
                    fills.append(fill)
                    position.apply_fill(fill)
                else:
                    # Limit/stop orders carry forward; market orders are dropped
                    if intent.order_type != OrderType.MARKET:
                        still_pending.append(intent)
            pending_intents = still_pending

            # Let strategy observe this bar (after fills are processed)
            new_intents_buffer = strategy.on_bar(bar, position, strategy_params)

            # Equity curve point (mark position at bar close)
            mark_pnl = position.unrealized_pnl(bar.close) + position.realized_pnl
            equity.append((bar.timestamp, mark_pnl))

            # Drawdown tracking
            if mark_pnl > peak_equity:
                peak_equity = mark_pnl
            dd = mark_pnl - peak_equity
            if dd < result.max_drawdown:
                result.max_drawdown = dd

            result.bars_processed += 1

        # Build result
        result.fills = fills
        result.equity_curve = equity
        result.final_position = position
        result.total_realized_pnl = position.realized_pnl
        result.total_costs = position.total_costs
        result.net_pnl = position.realized_pnl - position.total_costs
        result.num_trades = len(fills)
        result.sharpe_ratio = self._compute_sharpe(equity)

        logger.info(
            "backtest_complete",
            extra=result.summary(),
        )
        return result

    # ------------------------------------------------------------------ #

    def _simulate_fill(
        self,
        intent: OrderIntent,
        fill_bar: Bar,
        instrument: Instrument,
    ) -> Fill | None:
        """Attempt to fill an intent on fill_bar. Returns None if the order
        would not have been triggered (limit not reached, stop not hit)."""
        fill_price: Decimal | None = None

        if intent.order_type == OrderType.MARKET:
            # Fill at open of next bar + slippage
            fill_price = self._slippage.apply(
                fill_bar.open, intent.side, instrument.tick_size
            )

        elif intent.order_type == OrderType.LIMIT:
            assert intent.limit_price is not None
            # Limit buy fills if bar.low <= limit_price; sell if bar.high >= limit_price
            if intent.side == Side.BUY and fill_bar.low <= intent.limit_price:
                fill_price = self._slippage.apply(
                    intent.limit_price, intent.side, instrument.tick_size
                )
            elif intent.side == Side.SELL and fill_bar.high >= intent.limit_price:
                fill_price = self._slippage.apply(
                    intent.limit_price, intent.side, instrument.tick_size
                )

        elif intent.order_type in (OrderType.SL, OrderType.SL_M):
            assert intent.trigger_price is not None
            # Stop sell fills if bar.low <= trigger; stop buy fills if bar.high >= trigger
            if intent.side == Side.SELL and fill_bar.low <= intent.trigger_price:
                fill_price = self._slippage.apply(
                    intent.trigger_price, intent.side, instrument.tick_size
                )
            elif intent.side == Side.BUY and fill_bar.high >= intent.trigger_price:
                fill_price = self._slippage.apply(
                    intent.trigger_price, intent.side, instrument.tick_size
                )

        if fill_price is None:
            return None

        fill_price = instrument.round_to_tick(max(fill_price, Decimal("0.01")))
        costs = self._costs.compute(intent.side, fill_price, intent.quantity, instrument.lot_size)

        return Fill(
            fill_id=f"bt_{uuid.uuid4().hex[:12]}",
            client_order_id=f"qs_{intent.intent_sequence}",
            instrument_token=instrument.instrument_token,
            side=intent.side,
            quantity=intent.quantity,
            price=fill_price,
            source="backtest",
            executed_at=fill_bar.timestamp,
            **costs,
        )

    @staticmethod
    def _compute_sharpe(equity: list[tuple[datetime, Decimal]]) -> float | None:
        if len(equity) < 2:
            return None
        import numpy as np
        values = [float(v) for _, v in equity]
        returns = np.diff(values)
        std = float(np.std(returns, ddof=1))
        if std == 0:
            return None
        mean = float(np.mean(returns))
        # Annualise assuming daily bars (252 trading days)
        return float(mean / std * (252 ** 0.5))
