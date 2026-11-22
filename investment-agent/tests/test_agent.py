import json

import pytest

from agent.backtest import align, run_backtest
from agent.brokers import PaperBroker, kraken_signature
from agent.config import Asset, Config, load_config
from agent.engine import DAY, Engine, State
from agent.journal import Journal
from agent.llm_filter import parse_multipliers
from agent.market import Candle, to_eur
from agent.strategy import Signal, compute_signal, target_weights


class FakeMarket:
    def __init__(self, closes: dict[str, list[float]], prices: dict[str, float] | None = None):
        self.closes = closes
        self.prices = prices or {s: c[-1] for s, c in closes.items()}
        self.spreads: dict[str, float] = {}

    def spread(self, symbol):
        return self.spreads.get(symbol, 0.0)

    def price(self, symbol):
        return self.prices[symbol]

    def daily_closes(self, symbol):
        return self.closes[symbol]


def rising(n=80, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def falling(n=80, start=200.0, step=1.0):
    return [start - i * step for i in range(n)]


def make_engine(closes, cfg=None, llm=None, state=None, prices=None, journal=None):
    cfg = cfg or Config()
    broker = PaperBroker(cfg)
    market = FakeMarket(dict(closes), prices)
    return Engine(cfg, broker, market, state or State(), llm=llm, news=lambda: ["headline"],
                  journal=journal), broker, market


ALL_UP = {"BTC": rising(), "ETH": rising(), "SOL": rising()}


def test_signal_active_in_uptrend_and_inactive_in_downtrend():
    cfg = Config()
    assert compute_signal("BTC", rising(), cfg).active
    assert not compute_signal("BTC", falling(), cfg).active
    assert not compute_signal("BTC", rising(n=10), cfg).active  # too little history


def test_target_weights_respect_limits_and_llm_can_only_reduce():
    cfg = Config()
    sigs = [Signal(a.symbol, True, 100, 0.1, 0.3, "") for a in cfg.universe]
    w = target_weights(sigs, {"BTC": 5.0, "ETH": 0.5, "SOL": -1.0}, cfg)
    slot = cfg.max_exposure / 3
    assert w["BTC"] == pytest.approx(slot)  # multiplier above 1 is clamped
    assert w["ETH"] == pytest.approx(slot * 0.5)
    assert w["SOL"] == 0.0
    assert sum(w.values()) <= cfg.max_exposure
    assert max(w.values()) <= cfg.max_position_weight


def test_high_volatility_shrinks_position_and_blocked_assets_get_nothing():
    cfg = Config()
    sigs = [Signal("BTC", True, 100, 0.1, cfg.target_vol * 2, ""), Signal("ETH", True, 100, 0.1, 0.1, "")]
    w = target_weights(sigs, {}, cfg, blocked={"ETH"})
    assert w["BTC"] == pytest.approx(cfg.max_exposure / 3 / 2)
    assert w["ETH"] == 0.0


def test_cycle_buys_in_uptrend_and_stays_within_exposure():
    engine, broker, _ = make_engine(ALL_UP)
    result = engine.run_cycle(0.0)
    assert {f.symbol for f in result.fills} == {"BTC", "ETH", "SOL"}
    assert all(f.side == "buy" for f in result.fills)
    invested = sum(broker.get_qty(s) * 179.0 for s in ALL_UP)
    assert invested <= 1000 * Config().max_exposure + 0.01
    assert broker.get_cash() > 0
    assert result.equity == pytest.approx(1000, rel=0.01)  # only costs lost


def test_cycle_stays_in_cash_in_downtrend():
    engine, broker, _ = make_engine({"BTC": falling(), "ETH": falling(), "SOL": falling()})
    assert engine.run_cycle(0.0).fills == []
    assert broker.get_cash() == 1000


def test_second_cycle_without_changes_does_not_trade():
    engine, _, _ = make_engine(ALL_UP)
    engine.run_cycle(0.0)
    assert engine.run_cycle(900.0).fills == []


def test_position_is_sold_when_trend_ends():
    engine, broker, market = make_engine(ALL_UP)
    engine.run_cycle(0.0)
    market.closes["BTC"] = falling()
    market.prices["BTC"] = 175.0  # small dip, not a stop
    result = engine.run_cycle(900.0)
    assert [(f.symbol, f.side) for f in result.fills] == [("BTC", "sell")]
    assert broker.get_qty("BTC") == 0


def test_trailing_stop_sells_and_blocks_rebuy_during_cooldown():
    cfg = Config(max_drawdown=0.9)
    engine, broker, market = make_engine(ALL_UP, cfg)
    engine.run_cycle(0.0)
    market.prices["BTC"] = 179 * (1 - cfg.trailing_stop) - 1
    result = engine.run_cycle(900.0)
    assert ("BTC", "sell") in [(f.symbol, f.side) for f in result.fills]
    assert broker.get_qty("BTC") == 0
    market.prices["BTC"] = 179.0  # recovered, signal still active
    assert engine.run_cycle(1800.0).fills == []  # cooldown
    later = engine.run_cycle(1800.0 + cfg.stop_cooldown_days * DAY + 1)
    assert ("BTC", "buy") in [(f.symbol, f.side) for f in later.fills]


def test_kill_switch_liquidates_and_halts_until_reset():
    engine, broker, market = make_engine(ALL_UP)
    engine.run_cycle(0.0)
    market.prices = {s: p * 0.6 for s, p in market.prices.items()}
    result = engine.run_cycle(900.0)
    assert engine.state.halted
    assert all(broker.get_qty(s) == 0 for s in ALL_UP)
    assert any("KILL SWITCH" in n for n in result.notes)
    market.prices = {s: c[-1] for s, c in market.closes.items()}
    assert engine.run_cycle(1800.0).fills == []  # stays halted


def test_llm_filter_reduces_position_and_is_cached():
    class StubLLM:
        calls = 0

        def multipliers(self, signals, headlines):
            StubLLM.calls += 1
            return {"BTC": 0.0, "ETH": 1.0, "SOL": 1.0}, {"BTC": "exchange hack"}

    engine, broker, _ = make_engine(ALL_UP, llm=StubLLM())
    engine.run_cycle(1_000_000.0)
    engine.run_cycle(1_000_900.0)
    assert broker.get_qty("BTC") == 0 and broker.get_qty("ETH") > 0
    assert StubLLM.calls == 1
    engine.run_cycle(1_000_000.0 + 7 * 3600)
    assert StubLLM.calls == 2


def test_parse_multipliers_clamps_and_drops_unknown_symbols():
    text = json.dumps({"summary": "", "assets": [
        {"symbol": "BTC", "multiplier": 7, "reason": "x"},
        {"symbol": "ETH", "multiplier": -3, "reason": "y"},
        {"symbol": "DOGE", "multiplier": 1, "reason": "z"},
    ]})
    mults, reasons = parse_multipliers(text, ["BTC", "ETH"])
    assert mults == {"BTC": 1.0, "ETH": 0.0}
    assert set(reasons) == {"BTC", "ETH"}


def test_paper_broker_fees_persistence_and_overdraft(tmp_path):
    cfg = Config()
    path = tmp_path / "acct.json"
    broker = PaperBroker(cfg, path)
    fill = broker.market_order("BTC", "buy", 1.0, 100.0)
    assert fill.price == pytest.approx(100.1)
    assert broker.get_cash() == pytest.approx(1000 - 100.1 * 1.004)
    assert PaperBroker(cfg, path).get_qty("BTC") == 1.0
    with pytest.raises(ValueError):
        broker.market_order("BTC", "buy", 100.0, 100.0)
    broker.market_order("BTC", "sell", 5.0, 100.0)  # sells only what is held
    assert broker.get_qty("BTC") == 0


def test_kraken_signature_matches_documented_example():
    data = {"nonce": "1616492376594", "ordertype": "limit", "pair": "XBTUSD",
            "price": 37500, "type": "buy", "volume": 1.25}
    secret = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
    assert kraken_signature("/0/private/AddOrder", data, secret) == (
        "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
    )


def test_live_mode_is_locked_without_confirmation(monkeypatch):
    from agent.brokers import make_broker

    monkeypatch.delenv("LIVE_TRADING_CONFIRM", raising=False)
    with pytest.raises(SystemExit):
        make_broker(Config(mode="live"))


def test_config_rejects_unknown_keys_and_bad_mode(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"mdoe": "paper"}')
    with pytest.raises(ValueError):
        load_config(p)
    p.write_text('{"mode": "yolo"}')
    with pytest.raises(ValueError):
        load_config(p)


def test_backtest_runs_on_synthetic_history_without_lookahead():
    n = 200
    candles = {}
    for sym in ("BTC", "ETH", "SOL"):
        closes = [100 + i for i in range(n)]
        candles[sym] = [Candle(i * DAY, c - 1, c + 1, c - 2, c) for i, c in enumerate(closes)]
    result = run_backtest(Config(), candles)
    assert result.trades >= 3
    assert result.end > result.start  # steady uptrend must be profitable
    assert result.halts == 0


def test_every_order_carries_its_reason():
    cfg = Config(max_drawdown=0.9)
    engine, _, market = make_engine(ALL_UP, cfg)
    assert {f.reason for f in engine.run_cycle(0.0).fills} == {"trend signal"}
    market.closes["ETH"] = falling()
    market.prices["ETH"] = 175.0
    market.prices["BTC"] = 100.0
    reasons = {f.symbol: f.reason for f in engine.run_cycle(900.0).fills if f.side == "sell"}
    # SOL is trimmed too: after BTC's fall it is too large a share of the smaller equity
    assert reasons == {"BTC": "trailing stop", "ETH": "trend ended", "SOL": "rebalance"}


def test_kill_switch_and_llm_sells_are_labelled():
    engine, _, market = make_engine(ALL_UP)
    engine.run_cycle(0.0)
    market.prices = {s: p * 0.6 for s, p in market.prices.items()}
    assert {f.reason for f in engine.run_cycle(900.0).fills} == {"kill switch"}

    class Cut:
        mult = 1.0

        def multipliers(self, signals, headlines):
            return {"BTC": self.mult, "ETH": 1.0, "SOL": 1.0}, {}

    llm = Cut()
    engine, _, _ = make_engine(ALL_UP, llm=llm)
    engine.run_cycle(1_000_000.0)
    llm.mult = 0.0
    fills = engine.run_cycle(1_000_000.0 + 7 * 3600).fills
    assert [(f.symbol, f.reason) for f in fills] == [("BTC", "llm filter")]


def test_realised_profit_includes_costs_on_both_sides():
    cfg = Config(max_drawdown=0.9, trailing_stop=0.9)
    engine, broker, market = make_engine(ALL_UP, cfg)
    buy = next(f for f in engine.run_cycle(0.0).fills if f.symbol == "BTC")
    market.closes["BTC"] = falling()
    market.prices["BTC"] = 200.0
    sell = engine.run_cycle(900.0).fills[0]
    expected = sell.qty * sell.price - sell.fee - (buy.qty * buy.price + buy.fee)
    assert sell.pnl == pytest.approx(expected)
    assert sell.pnl > 0
    assert "BTC" not in engine.state.cost_basis


def test_wide_spread_postpones_orders_but_not_stops():
    cfg = Config(max_drawdown=0.9)
    engine, broker, market = make_engine(ALL_UP, cfg)
    market.spreads = {"BTC": cfg.max_spread * 2}
    result = engine.run_cycle(0.0)
    assert {f.symbol for f in result.fills} == {"ETH", "SOL"}
    assert any("postponed" in n for n in result.notes)
    market.spreads = {"ETH": cfg.max_spread * 2}
    market.prices["ETH"] = 100.0  # stop level breached while the spread is wide
    fills = engine.run_cycle(900.0).fills
    assert ("ETH", "trailing stop") in [(f.symbol, f.reason) for f in fills]


def test_journal_records_cycles_decisions_trades_and_stats(tmp_path):
    cfg = Config(max_drawdown=0.9, trailing_stop=0.9)
    journal = Journal(tmp_path / "agent.db")
    engine, _, market = make_engine(ALL_UP, cfg, journal=journal)
    engine.run_cycle(0.0)
    market.closes["BTC"] = falling()
    market.prices["BTC"] = 200.0  # sold with a profit
    market.closes["ETH"] = falling()
    market.prices["ETH"] = 170.0  # sold with a loss
    engine.run_cycle(900.0)

    count = lambda table: journal.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    assert count("cycles") == 2
    assert count("decisions") == 6
    assert count("trades") == 5
    row = journal.db.execute(
        "SELECT active, target_weight, reason FROM decisions WHERE symbol='BTC' ORDER BY time DESC"
    ).fetchone()
    assert row[0] == 0 and row[1] == 0.0 and "below" in row[2]

    stats = Journal(tmp_path / "agent.db").stats()  # survives a restart
    assert (stats["orders"], stats["sells"], stats["wins"], stats["losses"]) == (5, 2, 1, 1)
    assert stats["by_reason"][0][0] == "trend ended"


def test_journal_keeps_llm_answers_with_the_headlines_shown():
    class Stub:
        def multipliers(self, signals, headlines):
            return {"BTC": 0.5, "ETH": 1.0, "SOL": 1.0}, {"BTC": "exchange hack"}

    journal = Journal()
    engine, _, _ = make_engine(ALL_UP, llm=Stub(), journal=journal)
    engine.run_cycle(1_000_000.0)
    rows = journal.db.execute(
        "SELECT symbol, multiplier, reason, headlines FROM llm_calls ORDER BY symbol"
    ).fetchall()
    assert rows[0] == ("BTC", 0.5, "exchange hack", '["headline"]')
    assert len(rows) == 3


def many(n):
    return [Asset(f"S{i}") for i in range(n)]


def sig(symbol, momentum, active=True):
    return Signal(symbol, active, 100, momentum, 0.1, "")


def test_top_n_picks_strongest_and_buffer_keeps_held_positions():
    cfg = Config(universe=many(6), top_n=2, rank_buffer=1, max_position_weight=0.5)
    signals = [sig("S0", 0.5), sig("S1", 0.4), sig("S2", 0.3), sig("S3", 0.2),
               sig("S4", 0.9, active=False), sig("S5", 0.1)]
    w = target_weights(signals, {}, cfg)
    assert {s for s, x in w.items() if x > 0} == {"S0", "S1"}  # inactive S4 never chosen
    assert w["S0"] == pytest.approx(0.95 / 2)
    # S2 is rank 3: inside top_n + buffer, so a held S2 keeps its slot over newcomer S1
    w = target_weights(signals, {}, cfg, held={"S2"})
    assert {s for s, x in w.items() if x > 0} == {"S2", "S0"}
    # S3 is rank 4: outside the buffer, it is dropped
    w = target_weights(signals, {}, cfg, held={"S3"})
    assert {s for s, x in w.items() if x > 0} == {"S0", "S1"}


def test_market_filter_blocks_new_positions_but_keeps_held_ones():
    cfg = Config(universe=many(4), top_n=2)
    signals = [sig("S0", 0.5), sig("S1", 0.4), sig("S2", 0.3), sig("S3", 0.2)]
    w = target_weights(signals, {}, cfg, held={"S1"}, allow_new=False)
    assert {s for s, x in w.items() if x > 0} == {"S1"}


def test_engine_applies_market_filter_from_benchmark():
    cfg = Config(benchmark="IDX", regime_window=50)
    closes = dict(ALL_UP, IDX=falling())
    engine, broker, market = make_engine(closes, cfg)
    result = engine.run_cycle(0.0)
    assert result.fills == []
    assert any("market filter" in n for n in result.notes)
    market.closes["IDX"] = rising()
    assert len(engine.run_cycle(900.0).fills) == 3


def test_rebalance_cadence_skips_reranking_but_stops_still_fire():
    cfg = Config(rebalance_days=7, max_drawdown=0.9)
    engine, broker, market = make_engine(ALL_UP, cfg)
    engine.run_cycle(0.0)
    market.closes["ETH"] = falling()  # trend ended, but no re-ranking before day 7
    market.prices["BTC"] = 100.0  # stop breached
    fills = engine.run_cycle(2 * DAY).fills
    assert [(f.symbol, f.reason) for f in fills] == [("BTC", "trailing stop")]
    market.prices["ETH"] = 175.0
    later = engine.run_cycle(7 * DAY).fills
    assert ("ETH", "trend ended") in [(f.symbol, f.reason) for f in later]


def test_kill_switch_resumes_automatically_after_halt_days():
    cfg = Config(halt_days=30)
    engine, broker, market = make_engine(ALL_UP, cfg)
    engine.run_cycle(0.0)
    good = dict(market.prices)
    market.prices = {s: p * 0.6 for s, p in good.items()}
    engine.run_cycle(DAY)
    assert engine.state.halted
    market.prices = good
    assert engine.run_cycle(10 * DAY).fills == []
    result = engine.run_cycle(31 * DAY + 1)
    assert not engine.state.halted
    assert len(result.fills) == 3
    assert engine.state.peak_equity < 1000  # drawdown now counts from the resume level


def test_minimum_fee_applies_and_is_budgeted():
    cfg = Config(fee_rate=0.0005, min_fee_eur=1.25)
    broker = PaperBroker(cfg)
    assert broker.market_order("BTC", "buy", 1.0, 100.0).fee == 1.25
    assert broker.market_order("BTC", "sell", 1.0, 100.0).fee == 1.25
    cfg = Config(fee_rate=0.0005, min_fee_eur=1.25, max_position_weight=1.0, max_exposure=1.0,
                 universe=[Asset("BTC")], target_vol=10.0)
    engine, broker, _ = make_engine({"BTC": rising()}, cfg)
    engine.run_cycle(0.0)  # wants 100 % of equity: must still leave room for the fee
    assert broker.get_cash() >= 0


def test_align_fills_holidays_and_marks_days_before_listing():
    a = [Candle(0, 1, 1, 1, 1), Candle(DAY, 2, 2, 2, 2), Candle(2 * DAY, 3, 3, 3, 3)]
    b = [Candle(DAY, 9, 9, 9, 9)]
    out = align({"A": a, "B": b})
    assert out["B"][0] is None
    assert out["B"][2].close == 9 and out["B"][2].open == 9  # holiday repeats last close
    assert [c.close for c in out["A"]] == [1, 2, 3]


def test_backtest_ignores_assets_before_their_listing():
    n = 300
    old = [Candle(i * DAY, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(n)]
    young = [Candle(i * DAY, 50 + i, 51 + i, 49 + i, 50 + i) for i in range(250, n)]
    cfg = Config(universe=[Asset("OLD"), Asset("YOUNG")])
    result = run_backtest(cfg, {"OLD": old, "YOUNG": young})
    assert result.end > result.start
    assert "Buy & hold all 1, equal" in result.benchmarks  # YOUNG lacks full history


def test_usd_prices_are_converted_with_the_latest_known_rate():
    usd = [Candle(DAY, 110, 110, 110, 110), Candle(2 * DAY, 120, 120, 120, 120)]
    fx = [Candle(0, 1.0, 1.0, 1.0, 1.0), Candle(DAY, 1.1, 1.1, 1.1, 1.1)]
    eur = to_eur(usd, fx)
    assert eur[0].close == pytest.approx(100)
    assert eur[1].close == pytest.approx(120 / 1.1)  # no rate for day 2: previous one is used


def test_named_universe_profiles_load():
    for name, count in (("config.stocks.json", 100), ("config.etf.json", 4)):
        cfg = load_config(name)
        assert cfg.market == "yahoo" and len(cfg.universe) >= count
