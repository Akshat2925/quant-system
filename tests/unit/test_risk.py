"""Unit tests for circuit breakers and risk manager."""

from decimal import Decimal

import pytest

from quant_system.risk import CircuitBreaker, RiskManager, RiskViolation, TripReason


def test_circuit_breaker_not_tripped_initially():
    cb = CircuitBreaker("test", threshold=100.0, reason=TripReason.POSITION_CAP)
    assert not cb.is_tripped


def test_circuit_breaker_trips_on_breach():
    cb = CircuitBreaker("test", threshold=-1000.0, reason=TripReason.DAILY_LOSS_LIMIT)
    assert cb.check(-999.0)   # below threshold magnitude
    assert not cb.check(-1001.0)  # crossed
    assert cb.is_tripped


def test_circuit_breaker_reset():
    cb = CircuitBreaker("test", threshold=-100.0, reason=TripReason.DAILY_LOSS_LIMIT)
    cb.check(-200.0)
    assert cb.is_tripped
    cb.reset()
    assert not cb.is_tripped


def test_circuit_breaker_blocked_once_tripped():
    cb = CircuitBreaker("test", threshold=-100.0, reason=TripReason.DAILY_LOSS_LIMIT)
    cb.check(-200.0)
    # Even a "good" value returns False while tripped
    assert not cb.check(0.0)


def test_risk_manager_check_all_raises_on_tripped_breaker():
    rm = RiskManager(daily_loss_limit=Decimal("-1000"))
    rm.update_pnl(Decimal("-1500"))
    with pytest.raises(RiskViolation):
        rm.check_all()


def test_risk_manager_global_kill_switch():
    rm = RiskManager()
    assert not rm.is_halted
    rm.global_kill_switch("test")
    assert rm.is_halted
    with pytest.raises(RiskViolation):
        rm.check_all()


def test_risk_manager_reset_global_kill():
    rm = RiskManager()
    rm.global_kill_switch("test")
    rm.reset_global_kill()
    assert not rm.is_halted


def test_risk_manager_pnl_within_limit_does_not_trip():
    rm = RiskManager(daily_loss_limit=Decimal("-10000"))
    rm.update_pnl(Decimal("-5000"))
    rm.check_all()  # should not raise


def test_order_rate_limit():
    rm = RiskManager(max_orders_per_minute=3)
    assert rm.check_order_rate()
    assert rm.check_order_rate()
    assert rm.check_order_rate()
    # 4th order exceeds limit
    assert not rm.check_order_rate()
