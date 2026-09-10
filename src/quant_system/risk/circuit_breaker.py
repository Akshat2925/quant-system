"""Circuit breakers and kill switches.

A circuit breaker monitors a metric and trips when a threshold is crossed.
Once tripped, it blocks order placement until manually reset (or auto-reset
after a configurable cooldown).

Global kill switch: a single flag that stops all order placement immediately.
Per-instrument kill switches are managed on the strategy side (GridStrategy,
SARStrategy each have their own). This module owns the global one.

Design:
- All checks are synchronous and cheap (<1µs) — they run in the hot path.
- Thresholds are set at construction time; regime overrides call reset_threshold().
- Tripped state is logged via structlog to ensure audit trail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)


class TripReason(str, Enum):
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    POSITION_CAP = "POSITION_CAP"
    ORDER_RATE = "ORDER_RATE"
    MANUAL = "MANUAL"
    REGIME_CIRCUIT_HALT = "REGIME_CIRCUIT_HALT"


@dataclass
class CircuitBreakerEvent:
    reason: TripReason
    metric_value: float
    threshold: float
    tripped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reset_at: datetime | None = None
    note: str = ""


class CircuitBreaker:
    """A single monitored threshold that blocks trading when breached.

    Args:
        name: identifier, used in logs
        threshold: numeric limit (negative for loss limits)
        cooldown_seconds: auto-reset after this many seconds (0 = no auto-reset)
        reason: TripReason label for structured logs
    """

    def __init__(
        self,
        name: str,
        threshold: float,
        cooldown_seconds: int = 0,
        reason: TripReason = TripReason.MANUAL,
    ) -> None:
        self.name = name
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.reason = reason
        self._tripped = False
        self._tripped_at: datetime | None = None
        self.events: list[CircuitBreakerEvent] = []

    @property
    def is_tripped(self) -> bool:
        if not self._tripped:
            return False
        # Auto-reset check
        if self.cooldown_seconds > 0 and self._tripped_at is not None:
            elapsed = (datetime.now(timezone.utc) - self._tripped_at).total_seconds()
            if elapsed >= self.cooldown_seconds:
                self._reset_internal("auto_cooldown_expired")
                return False
        return True

    def check(self, value: float) -> bool:
        """Returns True if trading is allowed (breaker not tripped).
        Trips the breaker if value crosses threshold.
        """
        if self.is_tripped:
            return False
        if self._is_breached(value):
            self._trip(value)
            return False
        return True

    def trip_manual(self, note: str = "") -> None:
        self._trip(float("nan"), note=note, reason_override=TripReason.MANUAL)

    def reset(self) -> None:
        self._reset_internal("manual_reset")

    def reset_threshold(self, new_threshold: float) -> None:
        self.threshold = new_threshold
        logger.info("circuit_breaker_threshold_updated", extra={"cb_name": self.name, "threshold": new_threshold})

    # ------------------------------------------------------------------ #

    def _is_breached(self, value: float) -> bool:
        # For loss limits threshold is negative; for caps threshold is positive
        if self.threshold < 0:
            return value <= self.threshold
        return value >= self.threshold

    def _trip(self, value: float, note: str = "", reason_override: TripReason | None = None) -> None:
        self._tripped = True
        self._tripped_at = datetime.now(timezone.utc)
        ev = CircuitBreakerEvent(
            reason=reason_override or self.reason,
            metric_value=value,
            threshold=self.threshold,
            note=note,
        )
        self.events.append(ev)
        logger.critical(
            "circuit_breaker_tripped",
            extra={
                "cb_name": self.name,
                "reason": ev.reason,
                "value": value,
                "threshold": self.threshold,
                "note": note,
            },
        )

    def _reset_internal(self, reason: str) -> None:
        self._tripped = False
        self._tripped_at = None
        logger.info("circuit_breaker_reset", extra={"cb_name": self.name, "reason": reason})


class RiskManager:
    """Aggregates all circuit breakers and the global kill switch.

    Checked by the execution engine before every order placement.

    Usage:
        rm = RiskManager(daily_loss_limit=Decimal("-50000"), max_drawdown=Decimal("-100000"))
        rm.check_all()          # raises RiskViolation if any breaker is tripped
        rm.update_pnl(pnl)      # call after each fill
        rm.global_kill_switch() # trip everything immediately
    """

    def __init__(
        self,
        daily_loss_limit: Decimal = Decimal("-50000"),
        max_drawdown: Decimal = Decimal("-100000"),
        max_orders_per_minute: int = 60,
    ) -> None:
        self._global_kill = False
        self._global_kill_reason = ""

        self.daily_loss_cb = CircuitBreaker(
            name="daily_loss_limit",
            threshold=float(daily_loss_limit),
            reason=TripReason.DAILY_LOSS_LIMIT,
        )
        self.drawdown_cb = CircuitBreaker(
            name="max_drawdown",
            threshold=float(max_drawdown),
            reason=TripReason.MAX_DRAWDOWN,
        )
        self._order_count = 0
        self._order_window_start = datetime.now(timezone.utc)
        self._max_orders_per_minute = max_orders_per_minute

        self._breakers = [self.daily_loss_cb, self.drawdown_cb]

    @property
    def is_halted(self) -> bool:
        return self._global_kill or any(cb.is_tripped for cb in self._breakers)

    def check_all(self) -> None:
        """Raise RiskViolation if any circuit breaker is tripped."""
        if self._global_kill:
            raise RiskViolation(f"Global kill switch active: {self._global_kill_reason}")
        for cb in self._breakers:
            if cb.is_tripped:
                raise RiskViolation(f"Circuit breaker '{cb.name}' is tripped")

    def check_order_rate(self) -> bool:
        """Returns False if order rate limit would be exceeded."""
        now = datetime.now(timezone.utc)
        window_elapsed = (now - self._order_window_start).total_seconds()
        if window_elapsed >= 60:
            self._order_count = 0
            self._order_window_start = now
        self._order_count += 1
        if self._order_count > self._max_orders_per_minute:
            logger.warning("order_rate_limit_exceeded", extra={"count": self._order_count})
            return False
        return True

    def update_pnl(self, realized_pnl: Decimal, unrealized_pnl: Decimal = Decimal(0)) -> None:
        """Feed current P&L into loss-limit circuit breakers."""
        total = float(realized_pnl + unrealized_pnl)
        self.daily_loss_cb.check(total)
        self.drawdown_cb.check(total)

    def global_kill_switch(self, reason: str = "manual") -> None:
        """Immediately halt all trading. Requires manual reset."""
        self._global_kill = True
        self._global_kill_reason = reason
        for cb in self._breakers:
            cb.trip_manual(note=f"global_kill: {reason}")
        logger.critical("global_kill_switch_activated", extra={"reason": reason})

    def reset_global_kill(self) -> None:
        self._global_kill = False
        self._global_kill_reason = ""
        for cb in self._breakers:
            cb.reset()
        logger.warning("global_kill_switch_reset")

    def add_breaker(self, cb: CircuitBreaker) -> None:
        self._breakers.append(cb)


class RiskViolation(Exception):
    """Raised when the risk manager blocks an action."""
