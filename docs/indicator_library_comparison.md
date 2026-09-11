# Indicator Library Comparison & Design Decision

## Why quant-system uses a custom indicator implementation

This document records the technical evaluation of available Python indicator
libraries and the rationale for building custom implementations instead.

---

## Libraries evaluated

### TA-Lib

**What it is:** C library with Python bindings. The industry standard for
indicator computation, used in most retail algo-trading systems.

**Strengths:**
- Extremely fast (C under the hood)
- 150+ indicators, battle-tested
- Widely documented

**Limitations for this system:**
- Requires compiled C binaries — installation on Windows/ARM is consistently
  painful and breaks CI pipelines unless a pre-built wheel is available
- No `Decimal` support — all output is `float64`. For a system where the
  acceptance criterion is "matches to the paisa", mixing float indicators
  with Decimal P&L paths requires careful boundary management
- Black-box implementation — when an indicator behaves unexpectedly on edge
  cases (e.g. Wilder ATR seeding), there's no Python code to inspect or test
- Cannot be monkey-patched for deterministic backtest reproducibility

**Verdict:** Excellent for rapid prototyping. Not the right foundation for a
production system with strict reconciliation requirements.

---

### pandas-ta

**What it is:** Pure Python/pandas indicator library, ~130 indicators.

**Strengths:**
- No compiled dependencies — pip install works everywhere
- DataFrame-native API fits pandas-based backtesting workflows
- Readable source code

**Limitations:**
- Several indicators have subtle implementation differences from the
  "canonical" Wilder/standard definitions (RSI seeding, ATR first value)
- Performance is mediocre on large datasets (vectorised pandas, but not
  numpy-optimised)
- Has had periods of poor maintenance and open bug backlog
- Still float64 only

**Verdict:** Good for exploratory analysis. The implementation inconsistencies
make it unsuitable as the authoritative source for strategy signals where
"backtest matches live" must hold exactly.

---

### vectorbt

**What it is:** High-performance backtesting + indicator library built on
numpy and numba.

**Strengths:**
- Genuinely fast — numba JIT compilation, portfolio-level vectorisation
- Excellent for parameter optimisation sweeps (runs 1000s of parameter
  combinations efficiently)
- Good visualisation built in

**Limitations:**
- Heavy dependency footprint (numba, llvmlite)
- The backtesting model is vectorised over the full dataset — this makes
  path-dependent logic (pyramiding, reversal fills, position state) awkward
  to express correctly
- Walk-forward testing requires careful manual slicing to avoid lookahead
- Not designed for live trading integration — it's a research tool

**Verdict:** Best-in-class for parameter sweep research. Not suitable as the
execution-path backtester for this system because it cannot share the same
`Position.apply_fill` code path as the live engine — which is the core
"reconciles to live" guarantee.

---

### backtrader

**What it is:** Event-driven backtesting framework with broker simulation.

**Strengths:**
- Full order lifecycle simulation
- Live trading adapters exist (including Zerodha community adapters)
- Large community

**Limitations:**
- Python 2-era design — heavy use of metaclasses, `cerebro` god object
- Difficult to unit test individual components in isolation
- Performance is poor on tick-level data
- Maintenance has slowed significantly

**Verdict:** Was the right choice 5 years ago. The architecture makes it hard
to enforce the "single P&L implementation" principle cleanly.

---

## Decision: custom implementation

quant-system uses custom numpy implementations for all indicators because:

1. **Single source of truth**: every indicator is one function, one file,
   one set of tests. There is no question of which library's ATR is being
   used in backtest vs live.

2. **Testable to known values**: each indicator has unit tests with
   hand-computed expected values. TA-Lib and pandas-ta are themselves
   untested from our perspective.

3. **No external binary dependencies**: the CI pipeline installs cleanly
   on any Python 3.11+ environment with `pip install -e ".[dev]"`.

4. **Wilder smoothing consistency**: ATR and RSI both use the same Wilder
   alpha (`1/period`), matching the broker's published indicator definitions
   for MCX/NSE strategies.

5. **Extensible**: adding a custom Indian-market indicator (e.g. Open
   Interest momentum, delivery volume ratio) is a new file + tests, not a
   fork of a library.

The performance trade-off is acceptable: indicators run on bar-level data
(OHLCV arrays of 500–2000 bars), not tick streams. At that scale, pure numpy
is fast enough that the bottleneck is always broker I/O, not indicator math.

---

## When we would use external libraries

- **TA-Lib**: if we needed 100+ indicators rapidly for a research spike and
  were not yet in production
- **vectorbt**: for parameter optimisation sweeps (ATR multiplier grid search)
  where we want to test 10,000 combinations before choosing live parameters
- **pandas-ta**: for exploratory Jupyter analysis where exact Wilder seeding
  doesn't matter
