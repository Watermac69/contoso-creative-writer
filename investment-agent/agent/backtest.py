"""Backtest of the rule-based part on Kraken daily history.

Runs the same Engine as live trading, one cycle per day: signals use closes up
to the previous day, orders fill at the current day's open. The LLM filter is
not part of the backtest (its past answers cannot be reconstructed).
"""
from __future__ import annotations

from dataclasses import dataclass

from .brokers import PaperBroker
from .config import Config
from .engine import Engine, State
from .market import Candle, fetch_daily_candles


class HistoricalMarket:
    def __init__(self, candles: dict[str, list[Candle]]):
        self.candles = candles
        self.i = 0

    def price(self, symbol: str) -> float:
        return self.candles[symbol][self.i].open

    def daily_closes(self, symbol: str) -> list[float]:
        return [c.close for c in self.candles[symbol][: self.i]]


@dataclass
class BacktestResult:
    days: int
    start: float
    end: float
    max_drawdown: float
    trades: int
    fees: float
    halted: bool
    benchmarks: dict[str, tuple[float, float]]  # name -> (end value, max drawdown)

    @property
    def total_return(self) -> float:
        return self.end / self.start - 1


def max_drawdown(curve: list[float]) -> float:
    peak, worst = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        worst = max(worst, 1 - v / peak)
    return worst


def align(candles: dict[str, list[Candle]]) -> dict[str, list[Candle]]:
    """Keep only days present for every asset."""
    common = set.intersection(*(set(c.time for c in cs) for cs in candles.values()))
    return {s: [c for c in cs if c.time in common] for s, cs in candles.items()}


def run_backtest(cfg: Config, candles: dict[str, list[Candle]]) -> BacktestResult:
    candles = align(candles)
    symbols = [a.symbol for a in cfg.universe]
    n = len(candles[symbols[0]])
    warmup = max(cfg.trend_window, cfg.momentum_window + 1, cfg.vol_window + 1)
    if n <= warmup + 1:
        raise ValueError("not enough history for a backtest")

    market = HistoricalMarket(candles)
    broker = PaperBroker(cfg)
    engine = Engine(cfg, broker, market, State())
    curve, trades, fees = [], 0, 0.0
    for i in range(warmup, n):
        market.i = i
        result = engine.run_cycle(float(candles[symbols[0]][i].time))
        trades += len(result.fills)
        fees += sum(f.fee for f in result.fills)
        curve.append(result.equity)

    start = cfg.start_capital_eur
    cost = 1 - cfg.fee_rate - cfg.slippage  # one-off cost of buying the benchmark
    benchmarks = {}
    for name, members in [("Buy & hold BTC", symbols[:1]), ("Buy & hold all, equal", symbols)]:
        bench = [
            sum(start * cost / len(members) * candles[s][i].open / candles[s][warmup].open
                for s in members)
            for i in range(warmup, n)
        ]
        benchmarks[name] = (bench[-1], max_drawdown(bench))
    return BacktestResult(n - warmup, start, curve[-1], max_drawdown([start] + curve),
                          trades, fees, engine.state.halted, benchmarks)


def fetch_and_run(cfg: Config) -> BacktestResult:
    candles = {a.symbol: fetch_daily_candles(a.pair)[:-1] for a in cfg.universe}
    return run_backtest(cfg, candles)


def format_result(r: BacktestResult) -> str:
    lines = [
        f"Period: {r.days} days, start capital {r.start:.2f} EUR",
        f"{'Agent (rules only)':<24}{r.end:>10.2f} EUR  {r.total_return:>+8.1%}  max drawdown {r.max_drawdown:.1%}",
    ]
    for name, (end, dd) in r.benchmarks.items():
        lines.append(f"{name:<24}{end:>10.2f} EUR  {end / r.start - 1:>+8.1%}  max drawdown {dd:.1%}")
    lines.append(f"Trades: {r.trades}, fees and slippage paid: {r.fees:.2f} EUR"
                 + (" | kill switch triggered" if r.halted else ""))
    return "\n".join(lines)
