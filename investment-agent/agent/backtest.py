"""Backtest of the rule-based part on daily history.

Runs the same Engine as live trading, one cycle per day: signals use closes up
to the previous day, orders fill at the current day's open. The LLM filter is
not part of the backtest (its past answers cannot be reconstructed).

Known limits for stocks: the universe is today's index members, so companies
that fell out of the index are missing (survivorship bias, flatters results),
and bid/ask spreads are only approximated by cfg.slippage.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .brokers import PaperBroker
from .config import Config
from .engine import Engine, State
from .market import Candle, fetch_daily_candles, fetch_yahoo, to_eur

LOOKBACK = 420  # closes handed to the strategy; more than any window it uses


class HistoricalMarket:
    """Candle lists share one calendar; None marks days before an asset was listed."""

    def __init__(self, candles: dict[str, list[Candle | None]]):
        self.candles = candles
        self.i = 0

    def price(self, symbol: str) -> float:
        c = self.candles[symbol][self.i]
        return c.open if c else 0.0

    def spread(self, symbol: str) -> float:
        return 0.0  # historical spreads are unknown; costs are covered by cfg.slippage

    def daily_closes(self, symbol: str) -> list[float]:
        window = self.candles[symbol][max(0, self.i - LOOKBACK): self.i]
        return [c.close for c in window if c]


@dataclass
class BacktestResult:
    days: int
    years: float
    start: float
    end: float
    max_drawdown: float
    trades: int
    fees: float
    halts: int
    benchmarks: dict[str, tuple[float, float]]  # name -> (end value, max drawdown)
    yearly: dict[int, dict[str, float]] = field(default_factory=dict)  # year -> name -> return

    @property
    def total_return(self) -> float:
        return self.end / self.start - 1

    @property
    def cagr(self) -> float:
        return (self.end / self.start) ** (1 / self.years) - 1


def max_drawdown(curve: list[float]) -> float:
    peak, worst = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        worst = max(worst, 1 - v / peak)
    return worst


def align(candles: dict[str, list[Candle]]) -> dict[str, list[Candle | None]]:
    """Put every asset on the union calendar. Gaps (holidays) repeat the last
    close; days before the first candle stay None."""
    days = sorted({c.time for cs in candles.values() for c in cs})
    out = {}
    for sym, cs in candles.items():
        by_day, row, last = {c.time: c for c in cs}, [], None
        for d in days:
            c = by_day.get(d)
            if c is None and last is not None:
                c = Candle(d, last.close, last.close, last.close, last.close)
            row.append(c)
            last = c or last
        out[sym] = row
    return out


def yearly_returns(times: list[int], curve: list[float], start: float) -> dict[int, float]:
    out, prev = {}, start
    for i, t in enumerate(times):
        year = datetime.fromtimestamp(t, timezone.utc).year
        if i + 1 == len(times) or datetime.fromtimestamp(times[i + 1], timezone.utc).year != year:
            out[year] = curve[i] / prev - 1
            prev = curve[i]
    return out


def run_backtest(cfg: Config, candles: dict[str, list[Candle]]) -> BacktestResult:
    """candles: one list per universe symbol, plus cfg.benchmark if set."""
    candles = align(candles)
    symbols = [a.symbol for a in cfg.universe if a.symbol in candles]
    cfg.universe = [a for a in cfg.universe if a.symbol in candles]
    calendar = [c.time for c in next(cs for cs in candles.values() if cs[0])]
    n = len(calendar)
    warmup = max(cfg.trend_window, cfg.momentum_window + 1, cfg.vol_window + 1, cfg.regime_window)
    if n <= warmup + 1:
        raise ValueError("not enough history for a backtest")

    market = HistoricalMarket(candles)
    engine = Engine(cfg, PaperBroker(cfg), market, State())
    curve, trades, fees, halts, was_halted = [], 0, 0.0, 0, False
    for i in range(warmup, n):
        market.i = i
        result = engine.run_cycle(float(calendar[i]))
        trades += len(result.fills)
        fees += sum(f.fee for f in result.fills)
        curve.append(result.equity)
        halts += engine.state.halted and not was_halted
        was_halted = engine.state.halted

    start = cfg.start_capital_eur
    times = calendar[warmup:]
    cost = 1 - cfg.fee_rate - cfg.slippage  # one-off cost of buying the benchmark
    full = [s for s in symbols if candles[s][warmup]]  # listed for the whole period
    groups = [(f"Buy & hold {cfg.benchmark}", [cfg.benchmark])] if cfg.benchmark in candles \
        else [(f"Buy & hold {symbols[0]}", symbols[:1])]
    groups.append((f"Buy & hold all {len(full)}, equal", full))
    benchmarks, yearly = {}, {}
    for year, r in yearly_returns(times, curve, start).items():
        yearly.setdefault(year, {})["Agent"] = r
    for name, members in groups:
        bench = [
            sum(start * cost / len(members) * candles[s][i].open / candles[s][warmup].open
                for s in members)
            for i in range(warmup, n)
        ]
        benchmarks[name] = (bench[-1], max_drawdown(bench))
        for year, r in yearly_returns(times, bench, start).items():
            yearly[year][name] = r
    return BacktestResult(n - warmup, (times[-1] - times[0]) / 86400 / 365.25, start, curve[-1],
                          max_drawdown([start] + curve), trades, fees, halts, benchmarks, yearly)


def load_history(cfg: Config, history: str = "10y") -> dict[str, list[Candle]]:
    if cfg.market == "kraken":
        return {a.symbol: fetch_daily_candles(a.pair)[:-1] for a in cfg.universe}
    fx = fetch_yahoo("EURUSD=X", history)[0]
    today = int(time.time()) // 86400 * 86400
    wanted = [(a.symbol, a.currency) for a in cfg.universe]
    if cfg.benchmark and cfg.benchmark not in {s for s, _ in wanted}:
        wanted.append((cfg.benchmark, "EUR"))
    out = {}
    for symbol, currency in wanted:
        try:
            candles = fetch_yahoo(symbol, history)[0]
        except Exception as exc:  # noqa: BLE001
            print(f"[data] {symbol} skipped: {exc}")
            continue
        candles = [c for c in candles if c.time < today]
        out[symbol] = to_eur(candles, fx) if currency == "USD" else candles
        time.sleep(0.15)
    return out


def fetch_and_run(cfg: Config) -> BacktestResult:
    return run_backtest(cfg, load_history(cfg))


def format_result(r: BacktestResult) -> str:
    def cagr(end):
        return (end / r.start) ** (1 / r.years) - 1

    lines = [
        f"Period: {r.days} trading days ({r.years:.1f} years), start capital {r.start:.2f} EUR",
        f"{'':<28}{'end EUR':>10} {'total':>9} {'per year':>9} {'max drawdown':>13}",
        f"{'Agent (rules only)':<28}{r.end:>10.2f} {r.total_return:>+9.1%} {r.cagr:>+9.1%} {r.max_drawdown:>13.1%}",
    ]
    for name, (end, dd) in r.benchmarks.items():
        lines.append(f"{name:<28}{end:>10.2f} {end / r.start - 1:>+9.1%} {cagr(end):>+9.1%} {dd:>13.1%}")
    lines.append(f"Orders: {r.trades}, fees paid: {r.fees:.2f} EUR, kill switch triggered: {r.halts}x")
    if len(r.yearly) > 1:
        names = ["Agent", *r.benchmarks]
        lines.append("")
        lines.append("Year  " + "".join(f"{n[:26]:>28}" for n in names))
        for year, row in sorted(r.yearly.items()):
            lines.append(f"{year}  " + "".join(f"{row.get(n, 0):>+28.1%}" for n in names))
    return "\n".join(lines)
