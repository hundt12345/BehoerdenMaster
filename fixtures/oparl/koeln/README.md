# Beispieldaten (Köln, OParl)

Diese Dateien sind **Auszüge echter Antworten** der OParl-Schnittstelle des Ratsinformationssystems der Stadt Köln, abgerufen über `buergerinfo.stadt-koeln.de/oparl`. Sie dienen dazu, die Anwendung ohne Netzzugang auszuprobieren (`python -m behoerdenmaster demo`). Sie sind **kein vollständiger Datenbestand**.

Lizenz der Originaldaten: Datenlizenz Deutschland – Zero – Version 2.0.

| Datei | Inhalt | Hinweis |
|---|---|---|
| `vorlagen.json` | Vorlage 1724/2021 „Ratsinformationssystem: OParl-Schnittstelle veröffentlicht“ | vollständiges Objekt aus der Antwort |
| `sitzungen.json` | Sitzung 33698 (11. Sitzung des Rates, 08.10.2026) mit elf TOPs und Dokumenten; Sitzung 34706 (Sondersitzung AVR, 12.10.2026) mit fünf TOPs | **Auszug**: nur die TOPs, die vollständig in den Antworten vorlagen |
| `gremien.json` | Gremien „Rat“ (gr/1) und „Ausschuss Allgemeine Verwaltung und Rechtsfragen / Vergabe / Internationales“ (gr/74) | **Namen aus den Sitzungstiteln abgeleitet**, nicht aus einer Gremienantwort |

Bekannte Lücken dieser Auszüge: Die Vorlagen zu den genannten TOPs fehlen, daher ist deren Beratungsfolge nicht vollständig. Der Ortsbezug ist ebenfalls nicht enthalten. Für echte Bauvorhaben-Daten ist der Abruf mit dem Crawler nötig.

Zur Stand-Prüfung: Während der Entwicklung meldeten die Server der Stadt „Server ist nicht verfügbar“. Die Auszüge stammen aus Antworten, die vorher erfolgreich abgerufen wurden.
