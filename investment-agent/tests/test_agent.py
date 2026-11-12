import json

import pytest

from agent.backtest import run_backtest
from agent.brokers import PaperBroker, kraken_signature
from agent.config import Config, load_config
from agent.engine import DAY, Engine, State
from agent.llm_filter import parse_multipliers
from agent.market import Candle
from agent.strategy import Signal, compute_signal, target_weights


class FakeMarket:
    def __init__(self, closes: dict[str, list[float]], prices: dict[str, float] | None = None):
        self.closes = closes
        self.prices = prices or {s: c[-1] for s, c in closes.items()}

    def price(self, symbol):
        return self.prices[symbol]

    def daily_closes(self, symbol):
        return self.closes[symbol]


def rising(n=80, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def falling(n=80, start=200.0, step=1.0):
    return [start - i * step for i in range(n)]


def make_engine(closes, cfg=None, llm=None, state=None, prices=None):
    cfg = cfg or Config()
    broker = PaperBroker(cfg)
    market = FakeMarket(dict(closes), prices)
    return Engine(cfg, broker, market, state or State(), llm=llm, news=lambda: ["headline"]), broker, market


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
    assert not result.halted
