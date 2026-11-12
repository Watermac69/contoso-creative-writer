"""Rule-based signals and position sizing. Pure functions, no I/O."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config


@dataclass
class Signal:
    symbol: str
    active: bool
    price: float
    momentum: float
    vol: float
    reason: str


def realized_vol(closes: list[float], window: int) -> float:
    """Annualised volatility of daily log returns (crypto trades 365 days)."""
    xs = closes[-(window + 1):]
    rets = [math.log(b / a) for a, b in zip(xs, xs[1:])]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(365)


def compute_signal(symbol: str, closes: list[float], cfg: Config) -> Signal:
    need = max(cfg.trend_window, cfg.momentum_window + 1, cfg.vol_window + 1)
    if len(closes) < need:
        return Signal(symbol, False, closes[-1] if closes else 0.0, 0.0, 0.0, "not enough history")
    price = closes[-1]
    average = sum(closes[-cfg.trend_window:]) / cfg.trend_window
    momentum = price / closes[-(cfg.momentum_window + 1)] - 1
    vol = realized_vol(closes, cfg.vol_window)
    above = price > average
    active = above and momentum > 0
    reason = (
        f"price {'above' if above else 'below'} {cfg.trend_window}d average, "
        f"{cfg.momentum_window}d return {momentum:+.1%}"
    )
    return Signal(symbol, active, price, momentum, vol, reason)


def target_weights(
    signals: list[Signal],
    multipliers: dict[str, float],
    cfg: Config,
    blocked: set[str] = frozenset(),
) -> dict[str, float]:
    """Target share of equity per asset.

    Every asset has an equal slot. An inactive slot stays in cash. Slots shrink
    when the asset is more volatile than target_vol, and the LLM multiplier
    (clamped to 0..1) can shrink them further.
    """
    slot = min(cfg.max_position_weight, cfg.max_exposure / len(cfg.universe))
    weights: dict[str, float] = {}
    for s in signals:
        if not s.active or s.symbol in blocked:
            weights[s.symbol] = 0.0
            continue
        vol_scale = min(1.0, cfg.target_vol / s.vol) if s.vol > 0 else 1.0
        mult = min(1.0, max(0.0, multipliers.get(s.symbol, 1.0)))
        weights[s.symbol] = slot * vol_scale * mult
    return weights
