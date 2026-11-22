"""Brokers: a simulated paper account and the real Kraken account."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Config
from .market import KRAKEN, TIMEOUT


@dataclass
class Fill:
    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    price: float
    fee: float
    reason: str = ""
    pnl: float | None = None  # realised profit after costs, sells only


class PaperBroker:
    """Simulated account. Fills at the reference price plus slippage and fee."""

    def __init__(self, cfg: Config, path: Path | None = None):
        self.cfg = cfg
        self.path = path
        self.cash = cfg.start_capital_eur
        self.qty: dict[str, float] = {}
        if path and path.exists():
            data = json.loads(path.read_text())
            self.cash, self.qty = data["cash"], data["qty"]

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"cash": self.cash, "qty": self.qty}, indent=2))

    def get_cash(self) -> float:
        return self.cash

    def get_qty(self, symbol: str) -> float:
        return self.qty.get(symbol, 0.0)

    def _fee(self, value: float) -> float:
        return max(self.cfg.min_fee_eur, value * self.cfg.fee_rate)

    def market_order(self, symbol: str, side: str, qty: float, ref_price: float) -> Fill:
        if qty <= 0:
            raise ValueError("qty must be positive")
        if side == "buy":
            price = ref_price * (1 + self.cfg.slippage)
            cost = qty * price
            fee = self._fee(cost)
            if cost + fee > self.cash + 1e-9:
                raise ValueError("not enough cash")
            self.cash -= cost + fee
            self.qty[symbol] = self.get_qty(symbol) + qty
        elif side == "sell":
            qty = min(qty, self.get_qty(symbol))
            price = ref_price * (1 - self.cfg.slippage)
            proceeds = qty * price
            fee = self._fee(proceeds)
            self.cash += proceeds - fee
            self.qty[symbol] = self.get_qty(symbol) - qty
            if self.qty[symbol] < 1e-12:
                del self.qty[symbol]
        else:
            raise ValueError(side)
        self._save()
        return Fill(symbol, side, qty, price, fee)


def kraken_signature(url_path: str, data: dict, secret: str) -> str:
    """API-Sign header as specified in the Kraken REST documentation."""
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = url_path.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


class KrakenBroker:
    """Real orders on Kraken. Only constructed when live trading is unlocked.

    Use a Kraken account (or sub-account) that holds only the money the agent
    may manage: it treats the whole EUR balance as its capital. Create the API
    key with trading permission only, never with withdrawal permission.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.key = os.environ["KRAKEN_API_KEY"]
        self.secret = os.environ["KRAKEN_API_SECRET"]

    def _private(self, method: str, **data) -> dict:
        url_path = f"/0/private/{method}"
        data["nonce"] = str(int(time.time() * 1000))
        headers = {
            "API-Key": self.key,
            "API-Sign": kraken_signature(url_path, data, self.secret),
        }
        r = requests.post(KRAKEN + url_path, headers=headers, data=data, timeout=TIMEOUT)
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(f"Kraken {method}: {body['error']}")
        return body["result"]

    def get_cash(self) -> float:
        return float(self._private("Balance").get("ZEUR", 0.0))

    def get_qty(self, symbol: str) -> float:
        return float(self._private("Balance").get(self.cfg.asset(symbol).balance_key, 0.0))

    def market_order(self, symbol: str, side: str, qty: float, ref_price: float) -> Fill:
        self._private(
            "AddOrder",
            pair=self.cfg.asset(symbol).pair,
            type=side,
            ordertype="market",
            volume=f"{qty:.8f}",
        )
        # The real fill price is not queried; the reference price and the
        # configured fee rate are logged as an approximation.
        return Fill(symbol, side, qty, ref_price, qty * ref_price * self.cfg.fee_rate)


def make_broker(cfg: Config):
    if cfg.mode == "paper":
        return PaperBroker(cfg, Path(cfg.state_dir) / "paper_account.json")
    if cfg.market != "kraken":
        raise SystemExit("Live trading is only implemented for Kraken. Stocks and ETFs "
                         "run in paper mode until a broker connection is added.")
    if os.environ.get("LIVE_TRADING_CONFIRM") != "yes":
        raise SystemExit(
            "Live mode is locked. Set LIVE_TRADING_CONFIRM=yes together with "
            "KRAKEN_API_KEY and KRAKEN_API_SECRET to trade real money."
        )
    return KrakenBroker(cfg)
