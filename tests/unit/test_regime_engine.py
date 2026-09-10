"""Unit tests for the Macro Regime Engine."""

import pytest

from quant_system.core.enums import RegimeState
from quant_system.regime import MacroProxy, RegimeEngine


def make_engine(**overrides):
    return RegimeEngine(overrides=overrides)


def test_circuit_halt_on_high_vix():
    engine = make_engine()
    snapshot = engine.evaluate([MacroProxy("india_vix", 31.0)])
    assert snapshot.state == RegimeState.CIRCUIT_HALT


def test_high_vol_on_moderate_vix():
    engine = make_engine()
    # vix_high_vol threshold is 20; score = (vix-20)/10, need >0.5 → vix>25
    snapshot = engine.evaluate([MacroProxy("india_vix", 26.0)])
    assert snapshot.state == RegimeState.HIGH_VOL


def test_risk_on_high_rsi_good_breadth():
    engine = make_engine()
    proxies = [
        MacroProxy("india_vix", 14.0),
        MacroProxy("market_rsi", 62.0),
        MacroProxy("advance_decline_ratio", 2.0),
    ]
    snapshot = engine.evaluate(proxies)
    assert snapshot.state == RegimeState.RISK_ON


def test_risk_off_low_rsi_poor_breadth():
    engine = make_engine()
    proxies = [
        MacroProxy("india_vix", 14.0),
        MacroProxy("market_rsi", 38.0),
        MacroProxy("advance_decline_ratio", 0.5),
    ]
    snapshot = engine.evaluate(proxies)
    assert snapshot.state == RegimeState.RISK_OFF


def test_neutral_on_balanced_signals():
    engine = make_engine()
    proxies = [
        MacroProxy("india_vix", 14.0),
        MacroProxy("market_rsi", 50.0),
        MacroProxy("advance_decline_ratio", 1.0),
    ]
    snapshot = engine.evaluate(proxies)
    assert snapshot.state == RegimeState.NEUTRAL


def test_apply_overrides_merges_params():
    engine = RegimeEngine(
        overrides={"HIGH_VOL": {"atr_spacing_multiplier": 2.5, "position_cap_lots": 10}}
    )
    base = {"atr_spacing_multiplier": 1.5, "position_cap_lots": 20, "atr_period": 14}
    merged = engine.apply_overrides(base, RegimeState.HIGH_VOL)
    assert merged["atr_spacing_multiplier"] == 2.5
    assert merged["position_cap_lots"] == 10
    assert merged["atr_period"] == 14  # unchanged


def test_apply_overrides_does_not_mutate_base():
    engine = RegimeEngine(overrides={"HIGH_VOL": {"position_cap_lots": 10}})
    base = {"position_cap_lots": 20}
    engine.apply_overrides(base, RegimeState.HIGH_VOL)
    assert base["position_cap_lots"] == 20  # original unchanged


def test_trading_allowed_except_circuit_halt():
    engine = make_engine()
    assert engine.is_trading_allowed(RegimeState.RISK_ON)
    assert engine.is_trading_allowed(RegimeState.HIGH_VOL)
    assert engine.is_trading_allowed(RegimeState.NEUTRAL)
    assert not engine.is_trading_allowed(RegimeState.CIRCUIT_HALT)


def test_no_proxies_returns_neutral():
    engine = make_engine()
    snapshot = engine.evaluate([])
    assert snapshot.state == RegimeState.NEUTRAL


def test_circuit_halt_threshold_override():
    engine = RegimeEngine(thresholds={"vix_circuit": 50.0})
    # VIX at 35 should NOT trigger circuit halt with custom threshold
    snapshot = engine.evaluate([MacroProxy("india_vix", 35.0)])
    assert snapshot.state != RegimeState.CIRCUIT_HALT
