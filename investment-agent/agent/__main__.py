"""Command line: python -m agent {run|once|status|backtest|reset-halt}"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from .backtest import fetch_and_run, format_result
from .brokers import make_broker
from .config import load_config
from .engine import Engine, State
from .journal import Journal
from .llm_filter import LLMFilter
from .market import KrakenMarket, fetch_headlines


def build(cfg):
    state_dir = Path(cfg.state_dir)
    state_path = state_dir / "state.json"
    state = State.load(state_path)
    llm = LLMFilter(cfg)
    engine = Engine(
        cfg, make_broker(cfg), KrakenMarket(cfg), state,
        llm=llm if llm.enabled else None,
        news=lambda: fetch_headlines(cfg.news_feeds),
        journal=Journal(state_dir / "agent.db"),
    )
    return engine, state, state_path, llm


def cycle(engine, state, state_path) -> None:
    result = engine.run_cycle(time.time())
    state.save(state_path)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{stamp}] equity {result.equity:.2f} EUR, cash {result.cash:.2f} EUR")
    for note in result.notes:
        print(f"  {note}")
    for f in result.fills:
        pnl = f"" if f.pnl is None else f", result {f.pnl:+.2f}"
        print(f"  ORDER {f.side} {f.qty:.6f} {f.symbol} @ {f.price:.2f} "
              f"(fee {f.fee:.2f}{pnl}) - {f.reason}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent")
    parser.add_argument("command", choices=["run", "once", "status", "backtest", "reset-halt"])
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "backtest":
        print(format_result(fetch_and_run(cfg)))
        return

    engine, state, state_path, llm = build(cfg)

    if args.command == "reset-halt":
        state.halted, state.halt_reason, state.peak_equity = False, "", 0.0
        state.save(state_path)
        print("Halt cleared. The drawdown limit now counts from the current equity.")
    elif args.command == "status":
        market, broker = engine.market, engine.broker
        cash = broker.get_cash()
        total = cash
        print(f"Mode: {cfg.mode} | LLM filter: {'on' if llm.enabled else 'off'}")
        for a in cfg.universe:
            qty = broker.get_qty(a.symbol)
            if qty:
                value = qty * market.price(a.symbol)
                total += value
                print(f"  {a.symbol}: {qty:.6f} = {value:.2f} EUR")
        print(f"  Cash: {cash:.2f} EUR\n  Equity: {total:.2f} EUR (start {cfg.start_capital_eur:.2f})")
        s = engine.journal.stats()
        rate = f"{s['wins'] / s['sells']:.0%}" if s["sells"] else "n/a"
        print(f"  Orders: {s['orders']} | sells: {s['sells']} ({s['wins']} wins, {s['losses']} losses, "
              f"hit rate {rate}) | realised {s['realized_pnl']:+.2f} EUR | costs {s['fees']:.2f} EUR")
        for reason, count, pnl in s["by_reason"]:
            print(f"    {reason}: {count} sells, {pnl:+.2f} EUR")
        if state.halted:
            print(f"  HALTED: {state.halt_reason}")
        for sym, reason in state.llm_reasons.items():
            print(f"  LLM {sym} x{state.llm_multipliers.get(sym, 1.0):.2f}: {reason}")
    elif args.command == "once":
        cycle(engine, state, state_path)
    else:  # run
        print(f"Agent started in {cfg.mode} mode, cycle every {cfg.interval_minutes} min. "
              f"LLM filter: {'on' if llm.enabled else 'off (no ANTHROPIC_API_KEY)'}")
        while True:
            try:
                cycle(engine, state, state_path)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive through API hiccups
                print(f"[error] cycle failed, retrying next interval: {exc}")
            time.sleep(cfg.interval_minutes * 60)


if __name__ == "__main__":
    main()
