"""Market-structure trend following ("Markttechnik"). Pure functions, no I/O.

An uptrend is a sequence of higher swing highs and higher swing lows. A long
entry is the close breaking above the last swing high; the stop sits at the
last swing low and follows each new, higher swing low upwards.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config


SWING_LOOKBACK = 120


@dataclass
class Setup:
    symbol: str
    uptrend: bool
    breakout_age: int | None  # bars since the close first exceeded the last swing high
    swing_high: float
    swing_low: float  # stop level; 0.0 when unknown
    last_low: float  # low of the last completed bar
    momentum: float
    reason: str


def swing_points(bars, k: int):
    """Confirmed swing highs and lows as (index, price). A swing high is a bar
    whose high is the highest of the k bars before and after it, so it is only
    known k bars later."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        window = bars[i - k: i + k + 1]
        if bars[i].high == max(b.high for b in window) and \
                all(bars[i].high > b.high for b in window if b is not bars[i]):
            highs.append((i, bars[i].high))
        if bars[i].low == min(b.low for b in window) and \
                all(bars[i].low < b.low for b in window if b is not bars[i]):
            lows.append((i, bars[i].low))
    return highs, lows


def analyse(symbol: str, bars, cfg: Config) -> Setup:
    last_low = bars[-1].low if bars else 0.0
    n = cfg.momentum_window
    momentum = bars[-1].close / bars[-(n + 1)].close - 1 if len(bars) > n else 0.0
    bars = bars[-SWING_LOOKBACK:]  # the last swings are recent; older bars only cost time
    highs, lows = swing_points(bars, cfg.swing_bars)
    if len(highs) < 2 or len(lows) < 2:
        return Setup(symbol, False, None, 0.0, lows[-1][1] if lows else 0.0, last_low, momentum,
                     "not enough swing points")
    (_, h1), (hi, h2) = highs[-2:]
    (_, l1), (_, l2) = lows[-2:]
    uptrend = h2 > h1 and l2 > l1
    breakout_age = None
    for i in range(hi + 1, len(bars)):
        if bars[i].close > h2:
            breakout_age = len(bars) - 1 - i
            break
    structure = "higher highs and lows" if uptrend else "no uptrend structure"
    broke = "no breakout" if breakout_age is None else f"breakout {breakout_age} bars ago"
    return Setup(symbol, uptrend, breakout_age, h2, l2, last_low, momentum,
                 f"{structure}, {broke} over {h2:.2f}, stop {l2:.2f}")
