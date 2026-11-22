"""One decision cycle: prices -> risk checks -> signals -> LLM filter -> orders."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .brokers import Fill
from .config import Config
from .strategy import compute_signal, target_weights

DAY = 86400


@dataclass
class State:
    peak_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    high_water: dict[str, float] = field(default_factory=dict)  # highest price since entry
    cost_basis: dict[str, float] = field(default_factory=dict)  # average cost per unit, fees included
    cooldown_until: dict[str, float] = field(default_factory=dict)
    llm_time: float = 0.0
    llm_multipliers: dict[str, float] = field(default_factory=dict)
    llm_reasons: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "State":
        return cls(**json.loads(path.read_text())) if path.exists() else cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))


@dataclass
class CycleResult:
    equity: float
    cash: float
    fills: list[Fill]
    notes: list[str]


class Engine:
    def __init__(self, cfg: Config, broker, market, state: State, llm=None, news=None, journal=None):
        self.cfg, self.broker, self.market, self.state = cfg, broker, market, state
        self.llm = llm  # object with .multipliers(signals, headlines), or None
        self.news = news  # callable returning headlines, or None
        self.journal = journal  # Journal, or None (backtests)

    def _order(self, symbol: str, side: str, qty: float, price: float, reason: str,
               fills: list[Fill]) -> None:
        """Place an order, attach its reason and keep the cost basis up to date."""
        st = self.state
        held = self.broker.get_qty(symbol)
        fill = self.broker.market_order(symbol, side, qty, price)
        fill.reason = reason
        if side == "buy":
            old = st.cost_basis.get(symbol, 0.0) * held
            st.cost_basis[symbol] = (old + fill.qty * fill.price + fill.fee) / (held + fill.qty)
        else:
            basis = st.cost_basis.get(symbol, fill.price)
            fill.pnl = fill.qty * (fill.price - basis) - fill.fee
            if self.broker.get_qty(symbol) * price < 1.0:
                st.cost_basis.pop(symbol, None)
        fills.append(fill)

    def _sell_all(self, symbol: str, price: float, reason: str, fills: list[Fill]) -> None:
        qty = self.broker.get_qty(symbol)
        if qty * price >= 1.0:
            self._order(symbol, "sell", qty, price, reason, fills)
        self.state.high_water.pop(symbol, None)

    def _llm_multipliers(self, signals, now: float) -> dict[str, float]:
        if self.llm is None:
            return {}
        if now - self.state.llm_time >= self.cfg.llm_interval_hours * 3600:
            headlines = self.news() if self.news else []
            mults, reasons = self.llm.multipliers(signals, headlines)
            self.state.llm_time = now
            self.state.llm_multipliers, self.state.llm_reasons = mults, reasons
            if self.journal:
                self.journal.record_llm(now, mults, reasons, headlines)
        return self.state.llm_multipliers

    def run_cycle(self, now: float) -> CycleResult:
        cfg, st = self.cfg, self.state
        symbols = [a.symbol for a in cfg.universe]
        prices = {s: self.market.price(s) for s in symbols}
        qty = {s: self.broker.get_qty(s) for s in symbols}
        cash = self.broker.get_cash()
        equity = cash + sum(qty[s] * prices[s] for s in symbols)
        fills: list[Fill] = []
        notes: list[str] = []
        decisions: list[dict] = []

        # 1. Kill switch
        st.peak_equity = max(st.peak_equity, equity)
        if not st.halted and equity < st.peak_equity * (1 - cfg.max_drawdown):
            st.halted = True
            st.halt_reason = (
                f"equity {equity:.2f} fell more than {cfg.max_drawdown:.0%} "
                f"below its peak {st.peak_equity:.2f}"
            )
            for s in symbols:
                self._sell_all(s, prices[s], "kill switch", fills)
            notes.append(f"KILL SWITCH: {st.halt_reason}")
        if st.halted:
            notes.append("halted - no trading until 'reset-halt'")
            return self._finish(now, prices, fills, notes, decisions)

        # 2. Trailing stops (protective: executed regardless of spread)
        blocked = {s for s in symbols if st.cooldown_until.get(s, 0) > now}
        for s in symbols:
            if qty[s] * prices[s] < 1.0:
                st.high_water.pop(s, None)
                continue
            st.high_water[s] = max(st.high_water.get(s, prices[s]), prices[s])
            if prices[s] < st.high_water[s] * (1 - cfg.trailing_stop):
                notes.append(f"trailing stop {s} at {prices[s]:.2f}")
                self._sell_all(s, prices[s], "trailing stop", fills)
                st.cooldown_until[s] = now + cfg.stop_cooldown_days * DAY
                blocked.add(s)

        # 3. Signals, LLM filter, targets
        signals = [compute_signal(s, self.market.daily_closes(s), cfg) for s in symbols]
        mults = self._llm_multipliers(signals, now)
        targets = target_weights(signals, mults, cfg, blocked)
        spreads = {s: self.market.spread(s) for s in symbols}
        for sig in signals:
            s = sig.symbol
            notes.append(f"{s}: {sig.reason} -> target {targets[s]:.0%}")
            decisions.append({
                "symbol": s, "price": prices[s], "spread": spreads[s], "active": sig.active,
                "momentum": sig.momentum, "vol": sig.vol, "llm_multiplier": mults.get(s, 1.0),
                "target_weight": targets[s], "reason": sig.reason,
            })
        by_symbol = {sig.symbol: sig for sig in signals}

        # 4. Orders: sells first so the cash is there for buys
        qty = {s: self.broker.get_qty(s) for s in symbols}
        equity = self.broker.get_cash() + sum(qty[s] * prices[s] for s in symbols)
        deltas = {s: targets[s] * equity - qty[s] * prices[s] for s in symbols}
        for s in sorted(symbols, key=lambda x: deltas[x]):
            delta = deltas[s]
            closing = targets[s] == 0 and qty[s] * prices[s] >= 1.0
            small = abs(delta) < max(cfg.min_order_eur, cfg.rebalance_threshold * equity)
            if small and not closing:
                continue
            if spreads[s] > cfg.max_spread:
                notes.append(f"{s}: spread {spreads[s]:.2%} above limit, order postponed")
                continue
            sig = by_symbol[s]
            if delta < 0:
                if closing:
                    if s in blocked:
                        why = "stop cooldown"
                    elif not sig.active:
                        why = "trend ended"
                    else:
                        why = "llm filter"
                    self._sell_all(s, prices[s], why, fills)
                else:
                    why = "llm filter" if mults.get(s, 1.0) < 1.0 else "rebalance"
                    self._order(s, "sell", -delta / prices[s], prices[s], why, fills)
            else:
                budget = self.broker.get_cash() / (1 + cfg.fee_rate + cfg.slippage) * 0.999
                spend = min(delta, budget)
                if spend < cfg.min_order_eur:
                    continue
                why = "rebalance" if qty[s] * prices[s] >= 1.0 else "trend signal"
                self._order(s, "buy", spend / prices[s], prices[s], why, fills)
                st.high_water[s] = max(st.high_water.get(s, 0.0), prices[s])
        return self._finish(now, prices, fills, notes, decisions)

    def _finish(self, now, prices, fills, notes, decisions) -> CycleResult:
        cash = self.broker.get_cash()
        equity = cash + sum(self.broker.get_qty(s) * p for s, p in prices.items())
        if self.journal:
            self.journal.record_cycle(now, equity, cash, self.state.halted, decisions, fills)
        return CycleResult(equity, cash, fills, notes)
