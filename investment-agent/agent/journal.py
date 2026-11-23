"""SQLite journal: everything the agent saw, decided and did, for later analysis.

Tables
  cycles     one row per cycle: equity, cash, halted
  decisions  one row per asset and cycle: price, spread, signal, LLM multiplier, target
  trades     one row per order: side, qty, price, fee, reason, realised profit (sells)
  llm_calls  one row per asset and LLM call: multiplier, reason, headlines shown
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    time TEXT NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL, halted INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
    time TEXT NOT NULL, symbol TEXT NOT NULL, price REAL, spread REAL, active INTEGER,
    momentum REAL, vol REAL, llm_multiplier REAL, target_weight REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS trades (
    time TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL,
    price REAL NOT NULL, fee REAL NOT NULL, reason TEXT NOT NULL, pnl REAL);
CREATE TABLE IF NOT EXISTS llm_calls (
    time TEXT NOT NULL, symbol TEXT NOT NULL, multiplier REAL NOT NULL, reason TEXT,
    headlines TEXT);
CREATE INDEX IF NOT EXISTS idx_cycles_time ON cycles(time);
CREATE INDEX IF NOT EXISTS idx_decisions_time ON decisions(time, symbol);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time);
"""


def iso(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def record_cycle(self, now: float, equity: float, cash: float, halted: bool,
                     decisions: list[dict], fills: list) -> None:
        t = iso(now)
        with self.db:
            self.db.execute("INSERT INTO cycles VALUES (?,?,?,?)", (t, equity, cash, int(halted)))
            self.db.executemany(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(t, d["symbol"], d["price"], d["spread"], int(d["active"]), d["momentum"],
                  d["vol"], d["llm_multiplier"], d["target_weight"], d["reason"])
                 for d in decisions],
            )
            self.db.executemany(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)",
                [(t, f.symbol, f.side, f.qty, f.price, f.fee, f.reason, f.pnl) for f in fills],
            )

    def record_llm(self, now: float, multipliers: dict[str, float], reasons: dict[str, str],
                   headlines: list[str]) -> None:
        t, shown = iso(now), json.dumps(headlines, ensure_ascii=False)
        with self.db:
            self.db.executemany(
                "INSERT INTO llm_calls VALUES (?,?,?,?,?)",
                [(t, s, m, reasons.get(s, ""), shown) for s, m in multipliers.items()],
            )

    def stats(self) -> dict:
        """Hit rate over sells. A sell is a win when it realised a profit after costs."""
        row = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl > 0), 0), COALESCE(SUM(pnl), 0) "
            "FROM trades WHERE side = 'sell'"
        ).fetchone()
        total, fees = self.db.execute("SELECT COUNT(*), COALESCE(SUM(fee), 0) FROM trades").fetchone()
        by_reason = self.db.execute(
            "SELECT reason, COUNT(*), COALESCE(SUM(pnl), 0) FROM trades WHERE side = 'sell' "
            "GROUP BY reason ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {"orders": total, "fees": fees, "sells": row[0], "wins": row[1],
                "losses": row[0] - row[1], "realized_pnl": row[2], "by_reason": by_reason}
