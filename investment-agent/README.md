# Investment-Agent

Ein eigenständiger Handelsagent, der ein Startkapital (Standard: 1.000 €) rund um die Uhr in Krypto verwaltet. Er startet im **Paper-Trading**: echte Kraken-Kurse, simuliertes Geld.

> **Kein Gewinnversprechen.** Der Agent kann Geld verlieren, im Live-Betrieb echtes. Die eingebauten Grenzen begrenzen Verluste, verhindern sie aber nicht. Das hier ist Software, keine Anlageberatung.

## Wie er entscheidet

Jeder Zyklus (alle 15 Minuten) läuft in dieser Reihenfolge:

1. **Not-Aus:** Fällt das Vermögen mehr als 25 % unter seinen Höchststand, verkauft der Agent alles und handelt nicht mehr, bis du `reset-halt` ausführst.
2. **Trailing-Stop:** Fällt ein Wert 15 % unter seinen Höchstkurs seit dem Kauf, wird er verkauft und drei Tage gesperrt.
3. **Regeln (Trendfolge):** Ein Wert wird nur gehalten, wenn sein Kurs über dem 50-Tage-Durchschnitt liegt und die 30-Tage-Rendite positiv ist. Sonst bleibt sein Anteil in Cash.
4. **KI-Filter:** Claude liest aktuelle Schlagzeilen und kann eine Position verkleinern oder streichen, aber nie vergrößern. Fällt die KI aus oder fehlt der API-Schlüssel, läuft der Agent rein regelbasiert weiter.
5. **Positionsgröße:** BTC, ETH und SOL bekommen je einen gleich großen Anteil, zusammen höchstens 95 % des Vermögens und höchstens 40 % pro Wert. Schwankt ein Wert stärker als das Ziel, schrumpft sein Anteil.

Alle Werte stehen in `agent/config.py` und lassen sich in `config.json` überschreiben.

## Starten

```bash
cd investment-agent
pip install -r requirements.txt

python -m agent backtest     # Regeln an ~22 Monaten Kraken-Historie prüfen
python -m agent once         # einen Zyklus ausführen
python -m agent run          # Dauerbetrieb
python -m agent status       # Depot, Cash, letzte KI-Einschätzung
python -m agent reset-halt   # Not-Aus aufheben
python -m pytest             # Tests
```

Für den KI-Filter muss `ANTHROPIC_API_KEY` gesetzt sein. Er wird alle sechs Stunden befragt.

Kontostand, Trades und Vermögensverlauf liegen in `state/` (`paper_account.json`, `trades.csv`, `equity.csv`).

## 24/7-Betrieb

Der Agent braucht einen Rechner, der dauerhaft läuft, zum Beispiel einen kleinen Cloud-Server oder einen Raspberry Pi:

```bash
docker build -t investment-agent .
docker run -d --restart unless-stopped --name agent \
  -v "$PWD/state:/app/state" --env-file .env investment-agent
docker logs -f agent
```

`.env` legst du aus `.env.example` an.

## Backtest-Ergebnis

Stand 12.11.2026, Zeitraum 12.01.2025 bis 11.11.2026, 1.000 € Start, inklusive 0,4 % Gebühr und 0,1 % Slippage pro Order, ohne KI-Filter:

| | Endwert | Rendite | Größter Rückgang |
|---|---|---|---|
| Agent (nur Regeln) | 973,65 € | −2,6 % | 28,4 % |
| BTC kaufen und halten | 710,01 € | −29,0 % | 51,7 % |
| BTC, ETH, SOL zu gleichen Teilen halten | 562,51 € | −43,7 % | 62,9 % |

So ist das zu lesen:

- Der Agent hat in einem fallenden Markt Kapital geschont, aber **keinen Gewinn gemacht**.
- In diesem Lauf hat der Not-Aus ausgelöst; danach blieb der Agent in Cash. Ohne Not-Aus wären es +3,1 % bei 40,4 % Rückgang gewesen. Das Ergebnis hängt also spürbar an dieser einen Grenze.
- 70 Trades haben rund 83 € Kosten verursacht, bei 1.000 € ein großer Posten.
- Ein einzelner Zeitraum beweist nichts über die Zukunft. Die Parameter wurden nicht auf diese Daten optimiert.

## Live-Handel freischalten

Erst nach einigen Wochen Paper-Trading, und nur mit Geld, dessen Verlust du verkraftest.

1. Bei Kraken ein eigenes Konto oder Unterkonto anlegen, auf dem **nur** das Agenten-Kapital liegt. Der Agent betrachtet das gesamte EUR-Guthaben als sein Kapital.
2. API-Schlüssel mit Handelsrecht erstellen, **ohne Auszahlungsrecht**.
3. In `.env` setzen: `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `LIVE_TRADING_CONFIRM=yes`.
4. In `config.json` `"mode": "live"` setzen.

Der Live-Pfad ist noch **nicht gegen ein echtes Konto getestet**. Geprüft ist nur die Auftragssignatur gegen das Beispiel aus der Kraken-Dokumentation. Mindestordergrößen von Kraken werden nicht abgefragt, und im Log steht der Referenzkurs statt des tatsächlichen Ausführungskurses. Vor dem ersten echten Einsatz sollte das mit einem Kleinstbetrag verprobt werden.

## Später: Aktien und ETFs

Die Entscheidungslogik kennt nur zwei Schnittstellen: einen Markt (`price`, `daily_closes`) und einen Broker (`get_cash`, `get_qty`, `market_order`). Für Aktien und ETFs kommen ein weiterer Markt und ein weiterer Broker dazu (z. B. Alpaca oder Interactive Brokers) sowie eine Prüfung der Börsenzeiten.
