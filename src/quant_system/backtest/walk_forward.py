"""Walk-forward backtesting utilities.

Walk-forward testing avoids lookahead by training on an in-sample window
and testing on the immediately following out-of-sample window, rolling
forward through history. This is the standard validation methodology for
parameter-sensitive strategies like Grid (ATR multiplier) and SAR (stop mult).

No test window ever overlaps with or looks ahead of its training window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class WalkForwardWindow:
    """A single in-sample + out-of-sample split."""

    window_id: int
    in_sample_start: int    # inclusive bar index
    in_sample_end: int      # exclusive bar index
    out_sample_start: int   # = in_sample_end
    out_sample_end: int     # exclusive bar index


def walk_forward_splits(
    total_bars: int,
    in_sample_bars: int,
    out_sample_bars: int,
    step_bars: int | None = None,
) -> list[WalkForwardWindow]:
    """Generate walk-forward window splits.

    Args:
        total_bars      : total number of bars in the dataset
        in_sample_bars  : size of the in-sample training window
        out_sample_bars : size of the out-of-sample test window
        step_bars       : how many bars to step forward each time
                          (defaults to out_sample_bars → non-overlapping)

    Returns:
        List of WalkForwardWindow objects in chronological order.
        The first window starts at bar 0.
    """
    if in_sample_bars < 1 or out_sample_bars < 1:
        raise ValueError("in_sample_bars and out_sample_bars must be >= 1")
    if step_bars is None:
        step_bars = out_sample_bars

    windows: list[WalkForwardWindow] = []
    start = 0
    wid = 0

    while start + in_sample_bars + out_sample_bars <= total_bars:
        windows.append(WalkForwardWindow(
            window_id=wid,
            in_sample_start=start,
            in_sample_end=start + in_sample_bars,
            out_sample_start=start + in_sample_bars,
            out_sample_end=start + in_sample_bars + out_sample_bars,
        ))
        start += step_bars
        wid += 1

    return windows


def slice_bars(bars: Sequence[T], window: WalkForwardWindow, sample: str) -> list[T]:
    """Extract in-sample or out-of-sample bars for a window.

    Args:
        sample: "in" or "out"
    """
    if sample == "in":
        return list(bars[window.in_sample_start : window.in_sample_end])
    elif sample == "out":
        return list(bars[window.out_sample_start : window.out_sample_end])
    else:
        raise ValueError(f"sample must be 'in' or 'out', got: {sample!r}")
