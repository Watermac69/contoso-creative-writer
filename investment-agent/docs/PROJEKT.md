# Projektprotokoll Investment-Agent

Dieses Dokument hält fest, was entschieden wurde und warum, was offen ist und was als Nächstes ansteht. Die technische Beschreibung (Regeln, Befehle, Backtest-Tabellen) steht in der [README](../README.md); hier steht der Weg dorthin.

Stand: 23.11.2026

## Ziel

Ein Agent, der ein Startkapital selbstständig und ohne laufendes Zutun verwaltet. Er soll ordentliche Zuwächse erzielen, aber kein Alles-oder-nichts spielen. Ursprünglich war nur Krypto geplant; seit dem 22.11.2026 ist das Ziel breiter: Aktien und ETFs gehören dazu, Krypto ist ein Baustein unter mehreren.

## Aktueller Stand

- Vier Profile sind gebaut und laufen im Paper-Trading: Krypto, Aktien, ETFs, Markttechnik.
- 39 automatische Tests laufen durch. Im Repo gibt es keine CI für diesen Ordner, die Tests liefen nur lokal.
- Der Agent läuft noch nirgends dauerhaft. Bisher gab es nur einzelne Zyklen in der Entwicklungsumgebung.
- Echtes Geld ist nirgends im Einsatz.
- Der Code liegt im Branch `claude/autonomous-investment-agent-izb9gy`, Pull Request #1 (Entwurf).

## Entscheidungen

### Grundaufbau

| Entscheidung | Begründung |
|---|---|
| Erst Paper-Trading, Live-Handel gesperrt | Fehler sollen kein Geld kosten. Live braucht eigene Schlüssel und `LIVE_TRADING_CONFIRM=yes`. |
| Feste Regeln entscheiden, die KI filtert nur | Regeln sind im Backtest prüfbar, KI-Antworten nicht. |
| Die KI darf Positionen nur verkleinern, nie vergrößern | Schlagzeilen sind ungeprüfter Text aus dem Netz. Im schlimmsten Fall bleibt der Agent in Cash. |
| Harte Risikogrenzen: Stopp, Not-Aus, Obergrenze pro Position | „Maximaler Zuwachs" ohne Grenzen heißt maximales Risiko. |
| SQLite als Journal (`agent.db`) | Eine Datei, kein Server nötig. Hält Zyklen, Entscheidungen, Orders und KI-Antworten für spätere Auswertung fest. |
| Jede Order trägt ihren Grund | Später nachvollziehbar, warum gehandelt wurde und welche Regel Geld bringt oder kostet. |
| Eigener Ordner `investment-agent/` in diesem Repo | Das Repo ist thematisch etwas anderes; der Agent berührt keine bestehende Datei. |

### Krypto

| Entscheidung | Begründung |
|---|---|
| Kraken als Börse | Euro-Paare, gute Schnittstelle, prozentuale Gebühren ohne Mindestbetrag. |
| Nur BTC, ETH, SOL | Zehn Coins wurden getestet: −16,5 % statt −4,5 % im selben Zeitraum, weil kleinere Coins mehr Fehlsignale lieferten. |
| Not-Aus bei 25 %, manuelle Freigabe | Krypto schwankt stark; nach einem Einbruch soll ein Mensch draufschauen. |
| Spannen-Prüfung vor der Order | Keine Orders bei ungewöhnlich weitem Abstand zwischen Kauf- und Verkaufskurs. Stopp und Not-Aus verkaufen trotzdem. |

### Aktien und ETFs

| Entscheidung | Begründung |
|---|---|
| Yahoo Finance als Kursquelle | Zehn Jahre Historie für US- und deutsche Werte, kostenlos. Liefert keine Kauf-/Verkaufskurse. |
| Auswahl: rund 100 große US-Aktien und der DAX | Liquide Werte. Nachteil: Es sind die heutigen Indexmitglieder, das schönt den Backtest. |
| Aktien-Profil: die 5 stärksten nach 6-Monats-Rendite, wöchentlich | Gängiges Momentum-Verfahren. Fünf statt zehn Positionen, weil bei kleinem Kapital jede Order mindestens 1,25 € kostet. |
| Marktfilter: keine Neukäufe, wenn der Weltindex unter seinem 200-Tage-Durchschnitt liegt | Passt zum Wunsch nach begrenztem Risiko. |
| Not-Aus bei 20 % bzw. 15 %, automatische Wiederaufnahme nach 30 Tagen | Der Agent soll ohne Zutun weiterlaufen. |
| Parameter vorab festgelegt, nicht nachträglich optimiert | Wer an den Backtest-Daten dreht, bis das Ergebnis gut aussieht, misst nichts mehr. Empfindlichkeiten sind dokumentiert, aber nicht übernommen. |

### Markttechnik-Profil

| Entscheidung | Begründung |
|---|---|
| Eigenes Profil nach dem öffentlich beschriebenen Stil von Mario Lüddemann | Er ist ein Bekannter des Auftraggebers und handelt nach eigenen Angaben mit 30 bis 40 % pro Jahr. |
| Gebaut mit Standardannahmen: Tageschart, Einstieg beim Ausbruch, 1 % Risiko pro Trade | Seine genauen Regeln stehen nicht frei im Netz. Die Annahmen werden ersetzt, sobald seine Antworten vorliegen. |
| Ohne Hebel, nur steigende Kurse | Hebel und Wetten auf fallende Kurse erhöhen das Risiko und brauchen einen anderen Broker. |

### Betrieb

| Entscheidung | Begründung |
|---|---|
| Eigener Linux-Server mit Docker statt Azure | Der Server ist vorhanden. Azure-Containerdienste nutzen Netzlaufwerke, mit denen sich SQLite schlecht verträgt. |
| Alle vier Profile über eine Compose-Datei, Daten im Ordner `data/` | Ein Befehl zum Starten, Daten überleben Neustarts und Updates. |
| Dokumentation in GitHub, nicht in Notion | Alles an einer Stelle, mit dem Code versioniert. |

## Ergebnisse in Kürze

Die vollständigen Tabellen stehen in der README.

- **Krypto** (Januar 2025 bis November 2026): −4,5 %, während BTC 45,5 % verlor. Kapital geschont, kein Gewinn.
- **Aktien** (2017 bis 2026): +9,8 % pro Jahr bei 1.000 € Start, +14,8 % bei 10.000 €. Der Weltindex-ETF brachte +12,6 %. Mit 1.000 € schlägt der Agent den Index nicht, weil die Mindestgebühren zu viel kosten.
- **ETFs:** +3,3 % pro Jahr bei nur 11,2 % größtem Rückgang. Sicher, aber schwach.
- **Markttechnik:** −14,2 % pro Jahr bei 1.000 €, +0,2 % bei 10.000 €. Mit den Standardannahmen nicht brauchbar. Mit Hochs und Tiefs über fünf statt drei Tage sind es +5,2 %; die Zeitebene entscheidet viel.

Wichtigste Einschränkung: Die Aktien-Backtests enthalten nur heutige Indexmitglieder und sind dadurch zu gut.

## Recherche

### Mario Lüddemann

Quelle sind Suchergebnisse; die Seiten selbst waren aus der Entwicklungsumgebung nicht erreichbar.

- Hauptberuflicher Trader seit 1998, Geschäftsführer der Lüddemann Investments GmbH (Trading- und Investment-Ausbildung).
- „Trader des Jahres" 2020 und 2021 (Black Bull Award).
- Methode: Trendfolge nach Markttechnik mit striktem Risikomanagement, steigende und fallende Kurse, auch gehebelte Produkte.
- In einem Interview: 0,25 % Risiko pro Trade, höchstens 10 % des Kontos pro Position.
- Renditeangaben (rund 35 % pro Jahr für „Dein Millionendepot", 20 bis 40 % im Buchkonzept) stammen von ihm bzw. seinem Unternehmen; die 35 % sind eine Rückrechnung ohne Kosten und vor Steuern. Eine unabhängige Prüfung wurde nicht gefunden.

### Social-Media-Post „Grok-Bot macht aus 68 $ eine Dreiviertelmillion"

Als unglaubwürdig eingestuft: Die Rendite ist rechnerisch unmöglich, die beschriebene Technik ist für Arbitrage zu langsam, und der Screenshot zeigt ein Konto mit knapp 300 $ und Memecoins. Übernommen wurden nur drei Ideen aus dem abgebildeten Dashboard: Begründung pro Trade, Trefferquote, Prüfung vor der Order.

## Verworfen

| Idee | Warum nicht |
|---|---|
| Arbitrage zwischen Börsen | Gegen Firmen mit Servern direkt an der Börse nicht zu gewinnen. |
| Memecoins | Glücksspiel mit hohem Betrugsrisiko. |
| Zehn statt drei Coins | Im Backtest deutlich schlechter. |
| Agent, der „egal wie" Geld verdient oder Aufträge für andere annimmt | Kein Investment, sondern ein Dienstleistungsgeschäft mit Haftung beim Betreiber. Gegebenenfalls eigenes Projekt. |
| Azure-Containerdienste | Siehe Betrieb. |

## Offene Punkte

| Punkt | Wer | Bemerkung |
|---|---|---|
| Server-Instanz anlegen, Docker installieren, Agent starten | Auftraggeber, gemeinsam | Die Compose-Datei ist nur syntaktisch geprüft, noch nie gebaut oder gestartet. |
| Regeln von Mario Lüddemann einholen | Auftraggeber | Fragen siehe unten. |
| Markttechnik-Profil an seine Regeln anpassen und neu testen | Entwicklung | Wartet auf die Antworten. |
| Broker-Anbindung für Aktien und ETFs (z. B. Interactive Brokers) | Entwicklung | Ohne sie kein Echtgeld-Handel mit Aktien. Entscheidung für den Broker steht aus. |
| Börsenzeiten prüfen | Entwicklung | Der Paper-Handel bucht derzeit auch am Wochenende zum letzten Kurs. |
| Live-Pfad zu Kraken testen | gemeinsam | Nur die Auftragssignatur ist geprüft, Mindestordergrößen werden nicht abgefragt. |
| KI-Filter mit echtem Schlüssel testen | gemeinsam | Der echte Aufruf lief noch nie, nur die Verarbeitung der Antwort ist getestet. |
| Geldmarkt-ETF als Parkposition für nicht investiertes Geld | Entwicklung | Vorgeschlagen, nicht gebaut. |
| Steuerliche Behandlung klären | Auftraggeber | Bei Kraken und Interactive Brokers muss selbst erklärt werden. |
| Dokumentationsstruktur für die Zukunft festlegen | Auftraggeber | Soll an anderer Stelle geklärt werden. |

### Fragen an Mario Lüddemann

1. Auf welcher Zeitebene bestimmt er den Trend (Tages- oder Wochenchart)?
2. Steigt er beim Ausbruch über das letzte Hoch ein oder im Rücksetzer?
3. Wie sortiert er Fehlausbrüche aus?
4. Wann steigt er aus, im Gewinn und im Verlust?
5. Wie viel setzt er pro Position, und wie viel Hebel nutzt er typischerweise?

## Empfohlener Weg zu echtem Geld

1. Zwei bis vier Wochen alle Profile im Paper-Trading auf dem Server laufen lassen.
2. Danach höchstens 300 € live im Krypto-Profil auf einem eigenen Kraken-Konto, API-Schlüssel ohne Auszahlungsrecht. Das ist zugleich der erste echte Test des Live-Pfads.
3. Aktien erst mit Broker-Anbindung und ab etwa 5.000 €, weil darunter die Mindestgebühren die Rendite auffressen.
4. Das Markttechnik-Profil erst, wenn die echten Regeln eingebaut und getestet sind.

## Verlauf

| Datum | Was |
|---|---|
| 12.11.2026 | Krypto-Agent gebaut: Trendregeln, KI-Filter, Risikogrenzen, Paper-Konto, Backtest, 16 Tests. |
| 22.11.2026 | Begründung pro Order, Trefferquote, Spannen-Prüfung, SQLite-Journal. Zehn-Coin-Auswahl getestet und verworfen. Pull Request #1 angelegt. |
| 22.11.2026 | Aktien- und ETF-Profil mit Yahoo-Kursen, Rangliste, Marktfilter, Mindestgebühr, automatischer Wiederaufnahme; Backtest über neun Jahre. |
| 22.11.2026 | Recherche zu Mario Lüddemann. |
| 23.11.2026 | Markttechnik-Profil mit Standardannahmen; Backtest fällt schlecht aus. |
| 23.11.2026 | Compose-Datei für den Server-Betrieb, dieses Protokoll. |
