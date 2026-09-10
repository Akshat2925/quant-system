"""Macro Regime Engine.

Scores macro proxies (volatility, momentum, volume, breadth) into a
RegimeState, applies parameter overrides on top of base strategy params,
and enforces circuit breakers.

Architecture:
    MacroProxy      — raw data point (name, value, timestamp)
    RegimeScore     — intermediate per-dimension score [-1, 1]
    RegimeEngine    — combines scores → RegimeState, applies overrides

The scoring is table-driven (not embedded in strategy code), so changing
what "HIGH_VOL" means is a one-line config change, not a strategy refactor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from quant_system.core.enums import RegimeState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MacroProxy:
    """A single macro data point fed into the regime engine."""

    name: str                  # e.g. "india_vix", "nifty_rsi_14", "advance_decline"
    value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RegimeSnapshot:
    """Full regime assessment at a point in time."""

    state: RegimeState
    scores: dict[str, float]   # dimension -> score in [-1, 1]
    proxies: list[MacroProxy]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    notes: list[str] = field(default_factory=list)


# Default thresholds — override via config/strategy_params.yaml
_DEFAULT_THRESHOLDS: dict[str, Any] = {
    "vix_high_vol": 20.0,       # India VIX above this → HIGH_VOL candidate
    "vix_circuit": 30.0,        # India VIX above this → CIRCUIT_HALT candidate
    "rsi_risk_on": 55.0,        # Market RSI above this → RISK_ON momentum
    "rsi_risk_off": 45.0,       # Market RSI below this → RISK_OFF momentum
    "adx_trending": 25.0,       # ADX above this → trend present
    "ad_ratio_risk_on": 1.5,    # Advance/Decline ratio above this → RISK_ON breadth
    "ad_ratio_risk_off": 0.7,   # A/D ratio below this → RISK_OFF breadth
}


class RegimeEngine:
    """Scores macro proxies into a RegimeState and provides override params.

    Usage:
        engine = RegimeEngine(thresholds=..., overrides=...)
        snapshot = engine.evaluate([MacroProxy("india_vix", 22.3), ...])
        params = engine.apply_overrides(base_params, snapshot.state)
    """

    def __init__(
        self,
        thresholds: dict[str, float] | None = None,
        overrides: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._thresholds = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}
        # overrides: RegimeState value -> {param_name: override_value}
        self._overrides: dict[str, dict[str, Any]] = overrides or {}

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def evaluate(self, proxies: list[MacroProxy]) -> RegimeSnapshot:
        """Score proxies and return a RegimeSnapshot with the current state."""
        proxy_map = {p.name: p.value for p in proxies}
        scores: dict[str, float] = {}
        notes: list[str] = []

        # --- Circuit breaker: highest priority ---
        vix = proxy_map.get("india_vix")
        if vix is not None and vix >= self._thresholds["vix_circuit"]:
            notes.append(f"India VIX {vix:.1f} >= circuit threshold {self._thresholds['vix_circuit']}")
            state = RegimeState.CIRCUIT_HALT
            scores["volatility"] = 1.0
            return RegimeSnapshot(state=state, scores=scores, proxies=proxies, notes=notes)

        # --- Volatility score ---
        if vix is not None:
            if vix >= self._thresholds["vix_high_vol"]:
                scores["volatility"] = (vix - self._thresholds["vix_high_vol"]) / 10.0
                scores["volatility"] = min(scores["volatility"], 1.0)
                notes.append(f"High volatility: VIX={vix:.1f}")
            else:
                scores["volatility"] = -(self._thresholds["vix_high_vol"] - vix) / 20.0

        # --- Momentum score (RSI) ---
        rsi = proxy_map.get("market_rsi")
        if rsi is not None:
            if rsi >= self._thresholds["rsi_risk_on"]:
                scores["momentum"] = (rsi - 50.0) / 50.0
            elif rsi <= self._thresholds["rsi_risk_off"]:
                scores["momentum"] = -(50.0 - rsi) / 50.0
            else:
                scores["momentum"] = 0.0

        # --- Breadth score (advance/decline) ---
        ad_ratio = proxy_map.get("advance_decline_ratio")
        if ad_ratio is not None:
            if ad_ratio >= self._thresholds["ad_ratio_risk_on"]:
                scores["breadth"] = min((ad_ratio - 1.0) / 2.0, 1.0)
            elif ad_ratio <= self._thresholds["ad_ratio_risk_off"]:
                scores["breadth"] = max(-(1.0 - ad_ratio), -1.0)
            else:
                scores["breadth"] = 0.0

        # --- Trend score (ADX) ---
        adx = proxy_map.get("market_adx")
        if adx is not None:
            scores["trend"] = min((adx - 25.0) / 25.0, 1.0) if adx > 25.0 else 0.0

        # --- Determine state from scores ---
        state = self._classify(scores, notes)
        return RegimeSnapshot(state=state, scores=scores, proxies=proxies, notes=notes)

    def apply_overrides(
        self,
        base_params: dict[str, Any],
        state: RegimeState,
    ) -> dict[str, Any]:
        """Merge regime overrides on top of base strategy params.

        Returns a new dict (base_params is not mutated).
        """
        result = dict(base_params)
        override = self._overrides.get(state.value, {})
        if override:
            result.update(override)
            logger.info("regime_override", extra={"state": state.value, "override": override})
        return result

    def is_trading_allowed(self, state: RegimeState) -> bool:
        """Returns False when the regime state mandates a kill switch."""
        return state != RegimeState.CIRCUIT_HALT

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _classify(self, scores: dict[str, float], notes: list[str]) -> RegimeState:
        vol_score = scores.get("volatility", 0.0)
        momentum_score = scores.get("momentum", 0.0)
        breadth_score = scores.get("breadth", 0.0)

        if vol_score > 0.5:
            notes.append(f"Classified HIGH_VOL (vol_score={vol_score:.2f})")
            return RegimeState.HIGH_VOL

        composite = (momentum_score + breadth_score) / max(
            len([s for s in [momentum_score, breadth_score] if s != 0.0]), 1
        )

        if composite > 0.3:
            notes.append(f"Classified RISK_ON (composite={composite:.2f})")
            return RegimeState.RISK_ON
        elif composite < -0.3:
            notes.append(f"Classified RISK_OFF (composite={composite:.2f})")
            return RegimeState.RISK_OFF
        else:
            notes.append(f"Classified NEUTRAL (composite={composite:.2f})")
            return RegimeState.NEUTRAL
