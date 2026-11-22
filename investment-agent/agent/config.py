"""Configuration: defaults here, overrides from config.json."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Asset:
    symbol: str  # internal name, e.g. "BTC"; for the Yahoo market the Yahoo ticker
    pair: str = ""  # Kraken trading pair, e.g. "XBTEUR"
    balance_key: str = ""  # Kraken balance name, e.g. "XXBT"
    currency: str = "EUR"  # quote currency of the price data ("EUR" or "USD")


DEFAULT_UNIVERSE = [
    Asset("BTC", "XBTEUR", "XXBT"),
    Asset("ETH", "ETHEUR", "XETH"),
    Asset("SOL", "SOLEUR", "SOL"),
]


@dataclass
class Config:
    mode: str = "paper"  # "paper" or "live"
    market: str = "kraken"  # "kraken" (crypto) or "yahoo" (stocks and ETFs, paper only)
    start_capital_eur: float = 1000.0
    state_dir: str = "state"
    universe: list[Asset] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))

    # Strategy (daily candles)
    trend_window: int = 50  # price must be above this moving average
    momentum_window: int = 30  # and the return over this many days must be positive
    vol_window: int = 30
    target_vol: float = 0.60  # annualised; positions shrink when an asset is wilder
    periods_per_year: int = 365  # trading days per year: 365 for crypto, 252 for stocks
    top_n: int = 0  # hold only the N strongest assets by momentum (0 = every active asset)
    rank_buffer: int = 0  # a held asset is kept until it falls below rank top_n + rank_buffer
    rebalance_days: int = 0  # days between re-rankings (0 = every cycle); stops run every cycle
    benchmark: str = ""  # ticker for backtest comparison and the market filter below
    regime_window: int = 0  # no NEW positions while the benchmark is below this average (0 = off)

    # Risk limits
    max_position_weight: float = 0.40  # of equity, per asset
    max_exposure: float = 0.95  # of equity, all assets together
    trailing_stop: float = 0.15  # sell when price falls this far below its high since entry
    stop_cooldown_days: int = 3
    max_drawdown: float = 0.25  # kill switch: sell everything and halt
    halt_days: int = 0  # resume automatically this many days after a kill switch (0 = manual)
    rebalance_threshold: float = 0.05  # ignore weight differences smaller than this
    min_order_eur: float = 10.0
    max_spread: float = 0.005  # no new orders while bid/ask are further apart than this

    # Costs assumed in paper trading and backtests
    fee_rate: float = 0.004
    min_fee_eur: float = 0.0  # minimum fee per order
    slippage: float = 0.001

    # Loop
    interval_minutes: int = 15

    # LLM news filter (can only reduce exposure, never increase it)
    llm_enabled: bool = True
    llm_model: str = "claude-opus-5-5"
    llm_interval_hours: float = 6.0
    news_feeds: list[str] = field(
        default_factory=lambda: ["https://www.coindesk.com/arc/outboundfeeds/rss/"]
    )

    def asset(self, symbol: str) -> Asset:
        return next(a for a in self.universe if a.symbol == symbol)


def load_config(path: str | Path = "config.json") -> Config:
    cfg = Config()
    p = Path(path)
    if not p.exists():
        return cfg
    raw = json.loads(p.read_text())
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")
    for key, value in raw.items():
        if key == "universe":
            if isinstance(value, str):
                from .universes import UNIVERSES

                value = [Asset(symbol=t, currency=c) for t, c in UNIVERSES[value]]
            else:
                value = [Asset(**a) for a in value]
        setattr(cfg, key, value)
    if cfg.mode not in ("paper", "live"):
        raise ValueError("mode must be 'paper' or 'live'")
    if cfg.market not in ("kraken", "yahoo"):
        raise ValueError("market must be 'kraken' or 'yahoo'")
    return cfg
