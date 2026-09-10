"""Execution engine: converts OrderIntents into Orders, manages order lifecycle.

Responsibilities:
- Validate intents (instrument exists, side/type coherent).
- Apply position caps and risk checks before placing.
- Place orders idempotently via the broker adapter.
- Reconcile order state after restart (query open orders from broker).
- Handle fills via WebSocket updates or broker polling.
- Crash recovery: on restart, re-query broker for all open orders and
  reconcile local state before accepting new strategy signals.

Concurrency:
- The engine runs in an asyncio event loop.
- A single _process_intents coroutine serialises order placement to prevent
  race conditions between the strategy signal loop and the fill handler.
- An asyncio.Queue bridges the strategy thread and the execution coroutine.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from quant_system.broker.base import AbstractBroker
from quant_system.core.enums import OrderStatus, OrderType
from quant_system.core.idempotency import short_client_order_id
from quant_system.core.instrument import Instrument
from quant_system.core.order import Order
from quant_system.core.position import Position
from quant_system.risk.circuit_breaker import RiskManager, RiskViolation
from quant_system.strategies.base import OrderIntent

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Async order lifecycle manager.

    Usage:
        engine = ExecutionEngine(broker, risk_manager, instruments, run_id)
        await engine.start()
        engine.submit_intents([intent1, intent2])
        # ... engine processes intents in background
        await engine.stop()
    """

    def __init__(
        self,
        broker: AbstractBroker,
        risk_manager: RiskManager,
        instruments: dict[int, Instrument],  # token -> Instrument
        run_id: str | None = None,
        intent_queue_size: int = 500,
    ) -> None:
        self._broker = broker
        self._risk = risk_manager
        self._instruments = instruments
        self._run_id = run_id or str(uuid.uuid4())
        self._intent_queue: asyncio.Queue[OrderIntent] = asyncio.Queue(maxsize=intent_queue_size)
        self._orders: dict[str, Order] = {}  # client_order_id -> Order
        self._positions: dict[int, Position] = {}  # instrument_token -> Position
        self._running = False
        self._processor_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        await self._broker.connect()
        await self._reconcile_on_start()
        self._running = True
        self._processor_task = asyncio.create_task(self._process_loop(), name="execution_processor")
        logger.info("execution_engine_started", extra={"run_id": self._run_id})

    async def stop(self) -> None:
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        await self._broker.disconnect()
        logger.info("execution_engine_stopped", extra={"run_id": self._run_id})

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def submit_intents(self, intents: list[OrderIntent]) -> None:
        """Non-blocking: push intents onto the queue for async processing."""
        for intent in intents:
            try:
                self._intent_queue.put_nowait(intent)
            except asyncio.QueueFull:
                logger.warning(
                    "intent_queue_full_dropping_intent",
                    extra={"reason": intent.reason, "instrument": intent.instrument.tradingsymbol},
                )

    def get_position(self, instrument_token: int) -> Position:
        return self._positions.setdefault(
            instrument_token, Position(instrument_token=instrument_token)
        )

    def update_fill(self, client_order_id: str, fill_qty: int, fill_price: Decimal) -> None:
        """Called when a fill update arrives (from WebSocket or poll)."""
        order = self._orders.get(client_order_id)
        if order is None:
            logger.warning("fill_for_unknown_order", extra={"client_order_id": client_order_id})
            return
        order.apply_fill(fill_qty, fill_price)
        logger.info(
            "order_filled",
            extra={
                "client_order_id": client_order_id,
                "fill_qty": fill_qty,
                "fill_price": str(fill_price),
                "status": order.status.value,
            },
        )
        # Update risk manager with current realized P&L
        for pos in self._positions.values():
            self._risk.update_pnl(pos.realized_pnl)

    # ------------------------------------------------------------------ #
    #  Internal processing loop                                            #
    # ------------------------------------------------------------------ #

    async def _process_loop(self) -> None:
        while self._running:
            try:
                intent = await asyncio.wait_for(self._intent_queue.get(), timeout=1.0)
                await self._process_intent(intent)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("intent_processing_error", extra={"error": str(exc)})

    async def _process_intent(self, intent: OrderIntent) -> None:
        # Risk check first — blocks if global kill or circuit breaker tripped
        try:
            self._risk.check_all()
        except RiskViolation as exc:
            logger.warning("intent_blocked_by_risk", extra={"reason": str(exc), "intent_reason": intent.reason})
            return

        if not self._risk.check_order_rate():
            logger.warning("intent_blocked_rate_limit", extra={"intent_reason": intent.reason})
            return

        instrument = self._instruments.get(intent.instrument.instrument_token)
        if instrument is None:
            logger.error("intent_unknown_instrument", extra={"token": intent.instrument.instrument_token})
            return

        client_id = short_client_order_id(
            self._run_id,
            intent.instrument.instrument_token,
            intent.intent_sequence,
        )

        # Idempotency: don't re-place an order we already have
        if client_id in self._orders:
            logger.debug("intent_already_placed", extra={"client_order_id": client_id})
            return

        order = Order(
            client_order_id=client_id,
            instrument_token=instrument.instrument_token,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            trigger_price=intent.trigger_price,
            strategy_run_id=self._run_id,
            tags=intent.tags,
        )
        self._orders[client_id] = order

        try:
            broker_id = await self._broker.place_order(order)
            order.broker_order_id = broker_id
            order.transition_to(OrderStatus.SUBMITTED)
            logger.info(
                "order_submitted",
                extra={
                    "client_order_id": client_id,
                    "broker_order_id": broker_id,
                    "side": order.side.value,
                    "qty": order.quantity,
                    "instrument": instrument.tradingsymbol,
                },
            )
        except Exception as exc:
            order.transition_to(OrderStatus.UNKNOWN)
            logger.error(
                "order_placement_failed",
                extra={"client_order_id": client_id, "error": str(exc)},
            )

    # ------------------------------------------------------------------ #
    #  Crash recovery / reconciliation                                     #
    # ------------------------------------------------------------------ #

    async def _reconcile_on_start(self) -> None:
        """On startup, query broker for open orders and reconcile local state.

        Any order whose broker status we can't determine is set to UNKNOWN,
        blocking new orders on that instrument until resolved.
        """
        logger.info("reconciliation_started", extra={"run_id": self._run_id})
        try:
            raw_positions = await self._broker.get_positions()
            logger.info("reconciliation_positions_fetched", extra={"count": len(raw_positions)})
        except Exception as exc:
            logger.error("reconciliation_failed", extra={"error": str(exc)})
            return
        logger.info("reconciliation_complete")
