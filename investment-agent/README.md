# Investment-Agent

Ein eigenständiger Handelsagent, der ein Startkapital (Standard: 1.000 €) selbstständig verwaltet. Er startet im **Paper-Trading**: echte Kurse, simuliertes Geld.

> **Kein Gewinnversprechen.** Der Agent kann Geld verlieren, im Live-Betrieb echtes. Die eingebauten Grenzen begrenzen Verluste, verhindern sie aber nicht. Das hier ist Software, keine Anlageberatung.

Entscheidungen, offene Punkte und der Verlauf stehen im [Projektprotokoll](docs/PROJEKT.md).

## Vier Profile

| Profil | Start mit | Was er handelt | Kursquelle | Live-Handel |
|---|---|---|---|---|
| Krypto | `config.json` | BTC, ETH, SOL | Kraken | vorbereitet (Kraken), gesperrt |
| Aktien | `config.stocks.json` | die 5 stärksten aus rund 140 großen US- und DAX-Aktien | Yahoo Finance | noch nicht möglich |
| ETFs | `config.etf.json` | Aktien Welt, Schwellenländer, Anleihen, Gold | Yahoo Finance | noch nicht möglich |
| Markttechnik | `config.markttechnik.json` | Ausbrüche aus denselben rund 140 Aktien | Yahoo Finance | noch nicht möglich |

Jedes Profil hat ein eigenes Paper-Konto und Journal (`state/`, `state-stocks/`, `state-etf/`, `state-markttechnik/`). Alle Befehle unten nehmen `--config`, zum Beispiel `python -m agent run --config config.stocks.json`.

### So wählt das Aktien-Profil aus

- **Rangliste:** Einmal pro Woche werden alle Aktien nach ihrer 6-Monats-Rendite sortiert. Gekauft werden die fünf stärksten, die außerdem über ihrem 200-Tage-Durchschnitt liegen, je mit bis zu 19 % des Vermögens.
- **Halten:** Eine gehaltene Aktie bleibt, solange sie unter den zehn stärksten ist. Das vermeidet ständiges Umschichten.
- **Marktfilter:** Liegt der Weltindex-ETF (EUNL.DE) unter seinem 200-Tage-Durchschnitt, werden keine neuen Positionen eröffnet.
- **Ausstieg:** Trailing-Stop bei 20 %, Trendbruch oder Abrutschen in der Rangliste.
- **Not-Aus:** Bei 20 % Rückgang verkauft er alles und nimmt den Handel nach 30 Tagen von selbst wieder auf.
- **Kosten:** 0,05 % pro Order, mindestens 1,25 €, dazu 0,1 % Slippage. US-Kurse werden in Euro umgerechnet.

Das ETF-Profil nutzt dieselben Regeln ohne Rangliste, prüft alle vier Wochen und hat engere Grenzen (Stop 12 %, Not-Aus 15 %).

### So handelt das Markttechnik-Profil

Es folgt der Chartstruktur statt gleitenden Durchschnitten. Die Regeln sind Standardannahmen und noch nicht mit einem erfahrenen Markttechnik-Trader abgestimmt.

- **Trend:** Ein Aufwärtstrend ist eine Folge höherer Hochs und höherer Tiefs im Tageschart. Ein Hoch oder Tief zählt, wenn es die drei Tage davor und danach über- bzw. unterbietet.
- **Einstieg:** Kauf, wenn der Schlusskurs über das letzte Hoch ausbricht, höchstens drei Tage nach dem Ausbruch und nur, solange der Kurs noch darüber liegt.
- **Stopp:** am letzten Tief. Mit jedem neuen, höheren Tief wandert er nach oben.
- **Positionsgröße:** so, dass ein ausgelöster Stopp 1 % des Vermögens kostet, höchstens 20 % des Vermögens pro Aktie. Kein Hebel, nur steigende Kurse.
- **Marktfilter und Not-Aus:** wie beim Aktien-Profil.

## Wie er entscheidet (Krypto-Profil)

Jeder Zyklus (alle 15 Minuten) läuft in dieser Reihenfolge:

1. **Not-Aus:** Fällt das Vermögen mehr als 25 % unter seinen Höchststand, verkauft der Agent alles und handelt nicht mehr, bis du `reset-halt` ausführst.
2. **Trailing-Stop:** Fällt ein Wert 15 % unter seinen Höchstkurs seit dem Kauf, wird er verkauft und drei Tage gesperrt.
3. **Regeln (Trendfolge):** Ein Wert wird nur gehalten, wenn sein Kurs über dem 50-Tage-Durchschnitt liegt und die 30-Tage-Rendite positiv ist. Sonst bleibt sein Anteil in Cash.
4. **KI-Filter:** Claude liest aktuelle Schlagzeilen und kann eine Position verkleinern oder streichen, aber nie vergrößern. Fällt die KI aus oder fehlt der API-Schlüssel, läuft der Agent rein regelbasiert weiter.
5. **Positionsgröße:** BTC, ETH und SOL bekommen je einen gleich großen Anteil, zusammen höchstens 95 % des Vermögens und höchstens 40 % pro Wert. Schwankt ein Wert stärker als das Ziel, schrumpft sein Anteil.
6. **Spannen-Prüfung:** Liegen Kauf- und Verkaufskurs mehr als 0,5 % auseinander, wird die Order auf den nächsten Zyklus verschoben. Trailing-Stop und Not-Aus verkaufen trotzdem.

Jede Order bekommt einen Grund: `trend signal`, `rebalance`, `trend ended`, `llm filter`, `trailing stop`, `stop cooldown` oder `kill switch`.

Alle Werte stehen in `agent/config.py` und lassen sich in `config.json` überschreiben.

## Starten

```bash
cd investment-agent
pip install -r requirements.txt

python -m agent backtest     # Regeln an ~22 Monaten Kraken-Historie prüfen
python -m agent once         # einen Zyklus ausführen
python -m agent run          # Dauerbetrieb
python -m agent status       # Depot, Cash, Trefferquote, letzte KI-Einschätzung
python -m agent reset-halt   # Not-Aus aufheben
python -m pytest             # Tests
```

Für den KI-Filter muss `ANTHROPIC_API_KEY` gesetzt sein. Er wird alle sechs Stunden befragt.

## Journal (Datenbank)

Der Agent schreibt alles in eine SQLite-Datei, `state/agent.db`. Sie braucht keinen Server und lässt sich mit jedem SQLite-Werkzeug oder mit pandas auswerten.

| Tabelle | Inhalt |
|---|---|
| `cycles` | pro Zyklus: Vermögen, Cash, Not-Aus-Status |
| `decisions` | pro Wert und Zyklus: Kurs, Spanne, Signal, KI-Faktor, Zielanteil, Begründung |
| `trades` | pro Order: Menge, Kurs, Gebühr, Grund, bei Verkäufen der realisierte Gewinn nach Kosten |
| `llm_calls` | pro KI-Abfrage: Faktor und Begründung je Wert, dazu die gezeigten Schlagzeilen |

Beispiel:

```bash
sqlite3 state/agent.db "SELECT reason, COUNT(*), ROUND(SUM(pnl),2) FROM trades WHERE side='sell' GROUP BY reason"
```

Bei 15-Minuten-Zyklen wächst die Datei um etwa 400 Zeilen pro Tag. Der Kontostand des Paper-Kontos liegt daneben in `state/paper_account.json`.

## 24/7-Betrieb

Der Agent braucht einen Linux-Rechner mit Docker, der dauerhaft läuft und ins Internet darf. Eingehende Ports braucht er nicht. Alle vier Profile starten zusammen im Paper-Trading:

```bash
git clone https://github.com/Watermac69/contoso-creative-writer.git
cd contoso-creative-writer
git checkout claude/autonomous-investment-agent-izb9gy
cd investment-agent
docker compose up -d --build
docker compose logs -f                 # mitlesen
docker compose exec stocks python -m agent status --config config.stocks.json --state-root data
```

- **Daten:** Konten und Journale liegen im Ordner `data/` neben der Compose-Datei und überleben Neustarts und Updates. Diesen Ordner sichern.
- **Schlüssel:** optional in einer Datei `.env` (Vorlage: `.env.example`). Ohne `.env` läuft alles im Paper-Trading ohne KI-Filter.
- **Ausgehende Verbindungen:** `api.kraken.com`, `query1.finance.yahoo.com`, `www.coindesk.com`, für den KI-Filter `api.anthropic.com`.
- **Update:** `git pull && docker compose up -d --build`.

Auf Azure ist eine kleine Linux-VM die passende Form. Container-Dienste mit Netzlaufwerk (Azure Files) vertragen sich schlecht mit der SQLite-Datei des Journals.

## Backtest-Ergebnis Krypto

Stand 22.11.2026, Zeitraum 22.01.2025 bis 21.11.2026, 1.000 € Start, inklusive 0,4 % Gebühr und 0,1 % Slippage pro Order, ohne KI-Filter:

| | Endwert | Rendite | Größter Rückgang |
|---|---|---|---|
| Agent (nur Regeln) | 955,22 € | −4,5 % | 28,4 % |
| BTC kaufen und halten | 544,73 € | −45,5 % | 51,7 % |
| BTC, ETH, SOL zu gleichen Teilen halten | 426,82 € | −57,3 % | 62,4 % |

So ist das zu lesen:

- Der Agent hat in einem fallenden Markt Kapital geschont, aber **keinen Gewinn gemacht**.
- In diesem Lauf hat der Not-Aus ausgelöst; danach blieb der Agent in Cash. Ohne Not-Aus wären es +1,2 % bei 40,4 % Rückgang gewesen. Das Ergebnis hängt also spürbar an dieser einen Grenze.
- 69 Trades haben rund 81 € Kosten verursacht, bei 1.000 € ein großer Posten.
- Ein einzelner Zeitraum beweist nichts über die Zukunft. Die Parameter wurden nicht auf diese Daten optimiert.

**Breitere Auswahl geprüft und verworfen:** Mit zehn Werten (zusätzlich XRP, ADA, DOGE, LINK, LTC, DOT, AVAX) endet derselbe Backtest bei 835,33 € (−16,5 %, 238 Trades). Die kleineren Coins haben in diesem Zeitraum mehr Fehlsignale geliefert, deshalb bleibt es bei BTC, ETH und SOL.

## Backtest-Ergebnis Aktien und ETFs

Stand 22.11.2026, rund 9 Jahre (2017 bis 2026), inklusive Kosten, ohne KI-Filter:

| | Rendite pro Jahr | Größter Rückgang |
|---|---|---|
| Aktien-Profil, 1.000 € Start | +9,8 % | 26,6 % |
| Aktien-Profil, 10.000 € Start | +14,8 % | 24,8 % |
| ETF-Profil, 1.000 € Start | +3,3 % | 11,2 % |
| Weltindex-ETF kaufen und halten | +12,6 % | 33,2 % |
| Alle Aktien der Auswahl zu gleichen Teilen halten | +17,3 % | 32,2 % |

So ist das zu lesen:

- **Mit 1.000 € schlägt das Aktien-Profil den Weltindex nicht.** 548 Orders zu mindestens 1,25 € haben 685 € gekostet. Mit 10.000 € fallen dieselben Gebühren kaum ins Gewicht.
- **Die Auswahl ist geschönt.** Sie besteht aus den heutigen Indexmitgliedern. Firmen, die in den neun Jahren abgestiegen sind, fehlen. Deshalb liegt „alle halten" so hoch, und auch das Agenten-Ergebnis ist dadurch zu gut.
- **Das ETF-Profil ist sicher, aber schwach.** Es hatte den kleinsten Rückgang, hat aber die meisten Aufwärtsphasen nur teilweise mitgenommen.
- **Das Aktien-Profil schwankt weniger als der Markt** und hatte 2018 und 2020 klar bessere Jahre, 2019, 2021 und 2025 klar schlechtere.
- Die Parameter wurden vorab festgelegt und nicht auf diese Daten optimiert.

Nachrechnen: `python -m agent backtest --config config.stocks.json` (lädt zehn Jahre Kurse, dauert rund eine Minute).

### Markttechnik-Profil: mit den Standardannahmen nicht brauchbar

Derselbe Zeitraum, dieselben Kosten:

| | Rendite pro Jahr | Größter Rückgang | Orders |
|---|---|---|---|
| Markttechnik, 1.000 € Start | −14,2 % | 77,1 % | 656 |
| Markttechnik, 10.000 € Start | +0,2 % | 29,2 % | 1.713 |
| zum Vergleich: Tiefs und Hochs über 5 statt 3 Tage, 10.000 € | +5,2 % | 25,1 % | 1.260 |

- Die Regeln erzeugen sehr viele kurze Trades mit engen Stopps. Bei 1.000 € fressen die Mindestgebühren (820 €) das Konto auf.
- Auch ohne Gebührenproblem bleibt bei 10.000 € nichts übrig. Die Variante mit 5 Tagen ist besser, liegt aber weiter klar unter dem Aktien-Profil und dem Weltindex.
- Das Ergebnis hängt stark an der Zeitebene. Welche ein erfahrener Trader tatsächlich nutzt, ist die wichtigste offene Frage. Der Backtest dieses Profils dauert mehrere Minuten.

**Grenzen des Aktien- und ETF-Profils:** Der Paper-Handel bucht zum letzten bekannten Kurs, auch wenn die Börse geschlossen ist. Bruchteile von Aktien werden vorausgesetzt. Yahoo liefert keine Kauf- und Verkaufskurse, die Spannen-Prüfung greift hier also nicht.

## Live-Handel freischalten

Erst nach einigen Wochen Paper-Trading, und nur mit Geld, dessen Verlust du verkraftest.

1. Bei Kraken ein eigenes Konto oder Unterkonto anlegen, auf dem **nur** das Agenten-Kapital liegt. Der Agent betrachtet das gesamte EUR-Guthaben als sein Kapital.
2. API-Schlüssel mit Handelsrecht erstellen, **ohne Auszahlungsrecht**.
3. In `.env` setzen: `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `LIVE_TRADING_CONFIRM=yes`.
4. In `config.json` `"mode": "live"` setzen.

Der Live-Pfad ist noch **nicht gegen ein echtes Konto getestet**. Geprüft ist nur die Auftragssignatur gegen das Beispiel aus der Kraken-Dokumentation. Mindestordergrößen von Kraken werden nicht abgefragt, und im Log steht der Referenzkurs statt des tatsächlichen Ausführungskurses. Vor dem ersten echten Einsatz sollte das mit einem Kleinstbetrag verprobt werden.

## Später: Live-Handel für Aktien und ETFs

Die Entscheidungslogik kennt nur zwei Schnittstellen: einen Markt (`price`, `spread`, `daily_closes`) und einen Broker (`get_cash`, `get_qty`, `market_order`). Für echten Aktien- und ETF-Handel fehlt noch die Anbindung an einen Broker mit Programmierschnittstelle, zum Beispiel Interactive Brokers, sowie eine Prüfung der Börsenzeiten.
