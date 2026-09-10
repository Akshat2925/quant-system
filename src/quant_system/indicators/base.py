"""Base classes for all technical indicators.

Every indicator in this module follows the same contract:
- Accepts a numpy array or pandas Series of prices/values
- Returns a numpy array of the same length (NaN-padded for the warmup period)
- Uses only Decimal-safe integer math in critical paths; float is acceptable
  for indicator math (ATR, EMA etc.) since these are signals, not P&L.
- Each indicator has exactly ONE implementation — no duplicated math.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class Indicator(ABC):
    """Abstract base for all indicators."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def warmup_period(self) -> int:
        """Minimum number of bars needed before the first valid output."""
        ...

    @abstractmethod
    def compute(self, data: np.ndarray) -> np.ndarray:
        """Compute indicator values. Returns array of same length as input,
        NaN-padded for the warmup period."""
        ...
