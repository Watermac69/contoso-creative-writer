"""Market data: Kraken public API and news headlines."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import time
from dataclasses import dataclass

import requests

from .config import Config

KRAKEN = "https://api.kraken.com"
TIMEOUT = 20


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float


def kraken_public(method: str, **params) -> dict:
    r = requests.get(f"{KRAKEN}/0/public/{method}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(f"Kraken {method}: {body['error']}")
    return body["result"]


def fetch_daily_candles(pair: str) -> list[Candle]:
    """Up to 720 daily candles, oldest first. The last one is still forming."""
    result = kraken_public("OHLC", pair=pair, interval=1440)
    rows = next(v for k, v in result.items() if k != "last")
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]


class KrakenMarket:
    """Live market view used by the engine."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._tickers: dict[str, tuple[float, dict]] = {}

    def _ticker(self, symbol: str) -> dict:
        cached = self._tickers.get(symbol)
        if cached and time.monotonic() - cached[0] < 5:
            return cached[1]
        result = kraken_public("Ticker", pair=self.cfg.asset(symbol).pair)
        ticker = next(iter(result.values()))
        self._tickers[symbol] = (time.monotonic(), ticker)
        return ticker

    def price(self, symbol: str) -> float:
        return float(self._ticker(symbol)["c"][0])

    def spread(self, symbol: str) -> float:
        """Distance between best ask and best bid, relative to their midpoint."""
        t = self._ticker(symbol)
        ask, bid = float(t["a"][0]), float(t["b"][0])
        return (ask - bid) / ((ask + bid) / 2)

    def daily_closes(self, symbol: str) -> list[float]:
        candles = fetch_daily_candles(self.cfg.asset(symbol).pair)
        return [c.close for c in candles[:-1]]  # completed days only


def fetch_headlines(feeds: list[str], limit: int = 25) -> list[str]:
    """Recent headline titles. Failures are tolerated: news is optional."""
    titles: list[str] = []
    for url in feeds:
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.content[:2_000_000])
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                if title:
                    titles.append(title[:200])
        except Exception as exc:  # noqa: BLE001
            print(f"[news] {url}: {exc}")
    return titles[:limit]
