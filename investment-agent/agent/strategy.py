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


def realized_vol(closes: list[float], window: int, periods_per_year: int = 365) -> float:
    """Annualised volatility of daily log returns."""
    xs = closes[-(window + 1):]
    rets = [math.log(b / a) for a, b in zip(xs, xs[1:])]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def compute_signal(symbol: str, closes: list[float], cfg: Config) -> Signal:
    need = max(cfg.trend_window, cfg.momentum_window + 1, cfg.vol_window + 1)
    if len(closes) < need:
        return Signal(symbol, False, closes[-1] if closes else 0.0, 0.0, 0.0, "not enough history")
    price = closes[-1]
    average = sum(closes[-cfg.trend_window:]) / cfg.trend_window
    momentum = price / closes[-(cfg.momentum_window + 1)] - 1
    vol = realized_vol(closes, cfg.vol_window, cfg.periods_per_year)
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
    held: set[str] = frozenset(),
    allow_new: bool = True,
) -> dict[str, float]:
    """Target share of equity per asset.

    Without top_n every asset has an equal slot and an inactive slot stays in
    cash. With top_n there are top_n slots: held assets keep theirs while they
    stay active and within rank top_n + rank_buffer, free slots go to the
    strongest assets not yet held. New positions need allow_new (market filter).
    Slots shrink when the asset is more volatile than target_vol, and the LLM
    multiplier (clamped to 0..1) can shrink them further.
    """
    eligible = [s for s in signals if s.active and s.symbol not in blocked]
    if cfg.top_n:
        ranked = sorted(eligible, key=lambda s: s.momentum, reverse=True)
        keep = [s for i, s in enumerate(ranked)
                if s.symbol in held and i < cfg.top_n + cfg.rank_buffer]
        new = [s for s in ranked if s.symbol not in held] if allow_new else []
        chosen = (keep + new)[: max(cfg.top_n, len(keep))]
        slot = min(cfg.max_position_weight, cfg.max_exposure / cfg.top_n)
    else:
        chosen = [s for s in eligible if allow_new or s.symbol in held]
        slot = min(cfg.max_position_weight, cfg.max_exposure / len(cfg.universe))
    weights = {s.symbol: 0.0 for s in signals}
    for s in chosen:
        vol_scale = min(1.0, cfg.target_vol / s.vol) if s.vol > 0 else 1.0
        mult = min(1.0, max(0.0, multipliers.get(s.symbol, 1.0)))
        weights[s.symbol] = slot * vol_scale * mult
    return weights
