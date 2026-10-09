# BehoerdenMaster

Kommunale Vorgänge durchsuchen und nachvollziehbar zusammenfassen – zuerst für **Köln**, erweiterbar um weitere Städte und Quellen.

## Funktionen

- **Fragen in natürlicher Sprache**, etwa „Welche Bauvorhaben sind in Köln Ehrenfeld geplant?“: regelbasiertes Erkennen von Stadtteilen, Bezirken, Themen, Zeiträumen und „geplant/offen“.
- **PLZ-Suche**, optional mit Themenfilter.
- **Zwei Kölner Quellen in einer Suche:** Ratsinformation **OParl** und **Bauleitplanung Online NRW**.
- **Pro Vorgang:** Zusammenfassung, Status, Original-Link, Dokumentlinks und ein quellenabhängiges **Verlaufsprotokoll**:
  - OParl: Beratungsfolge mit Gremium, Sitzung, TOP und überliefertem Ergebnis.
  - Bauleitplanung: Beteiligungszeitraum, Phase und **lokal beobachtete Inhaltsänderungen**. Keine erfundene Ratsberatung und keine Behauptung einer vollständigen amtlichen Historie.
- Weboberfläche, FastAPI und CLI lesen ausschließlich die lokale SQLite-Datenbank. **Suchanfragen lösen keine Behördenabrufe aus.**

## Schnellstart – auch ohne Netz

Voraussetzungen: Python 3.11 oder neuer; Befehle im ausgecheckten Repository ausführen.

```bash
python3 -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest -q                        # Tests mit simulierten Quellen, keine Live-Abfragen
python -m behoerdenmaster demo
python -m behoerdenmaster frage "Welche Bauvorhaben sind in Köln Ehrenfeld geplant?"
python -m behoerdenmaster plz 50827 --thema bauen
python -m behoerdenmaster serve   # http://127.0.0.1:8000
```

Die **klar gekennzeichnete Offline-Demo (Stand 09.10.2026)** enthält eine OParl-Vorlage sowie drei ausgewählte öffentliche Bauleitverfahren:

- „Sicherung der Clubkultur …“ in Ehrenfeld (254. FNP-Änderung), mit ausgewählten Dokumentlinks.
- „Neues Quartier Bickendorf“, einschließlich der in der Quelle genannten ca. 1.600 Wohneinheiten und des voraussichtlichen Zeitplans.
- Die zugehörige 255. FNP-Änderung in Bickendorf.

Ehrenfeld bezeichnet Stadtteil **und** Stadtbezirk; Bickendorf gehört zum Stadtbezirk Ehrenfeld. Die Frage sucht beide Bedeutungen und erklärt dies. Die Demo ist **kein vollständiger oder laufend aktualisierter Bestand**. Bauleitbeschreibungen sind normalisierte, gekürzte Originalauszüge; das Detail-HTML ist eine rekonstruierte Testseite, kein unveränderter Mitschnitt. Herkunft: [`fixtures/oparl/koeln/README.md`](fixtures/oparl/koeln/README.md) und [`fixtures/bauleitplanung/koeln/README.md`](fixtures/bauleitplanung/koeln/README.md).

Erneutes Laden der Demo überschreibt keine bereits gespeicherten echten Objekte. Ein echter Bauleitabruf ersetzt passende Demo-Objekte, ohne deren Demo-Historie oder ungeprüfte Demo-Dokumentlinks als live erfasste Daten zu übernehmen.

```bash
python -m behoerdenmaster demo --loeschen  # entfernt beide Demo-Quellen, nicht die echten Daten
```

Die Datenbank liegt standardmäßig unter `data/behoerdenmaster.sqlite`; `--db` oder `BEHOERDEN_DB` ändern den Pfad. **Globale Optionen stehen vor dem Unterbefehl**, z. B. `python -m behoerdenmaster --db data/test.sqlite demo`. Außerhalb dieses Repositorys muss `BEHOERDEN_CONFIG_DIR` auf die Stadtkonfigurationen zeigen; die Demo-Fixtures sind Bestandteil des Repositorys, nicht des installierten Wheels.

## Echte Daten abrufen

Jeder Abruf benötigt eine eigene, erreichbare Kontaktadresse im User-Agent:

```bash
export BEHOERDEN_KONTAKT="it@ihre-organisation.example"   # durch die eigene Adresse ersetzen
```

### Bauleitplanung Online Köln

```bash
python -m behoerdenmaster probe --quelle bauleitplanung
python -m behoerdenmaster crawl --quelle bauleitplanung --max-anfragen 15
python -m behoerdenmaster crawl --quelle bauleitplanung --max-anfragen 15  # ggf. fortsetzen
python -m behoerdenmaster status
```

Der Adapter verwendet die **öffentliche JSON-Suche**, begrenzt auf Köln:

`https://nw.bauleitplanung-online.de/verfahren/suche/ajax?orgaSlug=koeln`

Er verarbeitet nur die öffentliche Sicht (`external*`-Felder), prüft zusätzlich die Organisation und ruft danach öffentliche HTML-Detailseiten für Dokumentlinks ab. **Keine Anmeldung, internen Verfahrensdaten, Stellungnahmen oder PDF-Downloads.** Folgelinks müssen auf derselben Suchroute und mit demselben Organisationsfilter bleiben.

Diese Quelle bietet keinen geprüften Änderungsfilter. Ein **abgeschlossener Folgelauf liest die ganze aktuell öffentliche Liste erneut**; unveränderte Inhalte erzeugen keine zusätzlichen Verlaufsschritte. `--seit` und `--nur` werden für Bauleitplanung ausdrücklich abgewiesen. Nach Budgetabbruch werden Listen-Cursor und Detail-Warteschlange fortgesetzt; eine bereits vollständig gelesene Liste wird dabei nicht unnötig erneut geladen.

Nicht mehr gelistete Verfahren bleiben gespeichert und werden entsprechend markiert – **erst nach einer vollständig gelesenen Liste**. Das Verschwinden aus der Liste bedeutet weder Genehmigung noch Bauabschluss. Ein 404/410 der Detailseite entfernt keine zuletzt bekannten Dokumentlinks; Login- und unbekannte Detailseiten ersetzen den Dokumentbestand ebenfalls nicht.

### Ratsinformationssystem / OParl

**OParl bleibt die Standardquelle**, wenn `--quelle` fehlt. Vor einem größeren Erstabruf die zulässigen Grenzen mit der Stadt abstimmen (Kontakt aus dem OParl-System: `12-session@stadt-koeln.de`).

```bash
python -m behoerdenmaster probe --quelle ris
python -m behoerdenmaster crawl --quelle ris --seit 2025-11-01 --max-anfragen 300
python -m behoerdenmaster crawl --quelle ris --max-anfragen 300  # ggf. fortsetzen
python -m behoerdenmaster crawl --quelle ris                    # danach nur Änderungen
```

Bei OParl gilt `--seit` für den ersten Lauf; spätere Läufe nutzen den gespeicherten Änderungsstand mit einer Stunde Überlappung.

### Verhalten gegenüber beiden Quellen

| Regel | Umsetzung |
|---|---|
| Erkennbarer Abrufer | User-Agent `BehoerdenMaster/0.1 (+Projekt-URL; Kontakt: …)`, auch für robots.txt |
| robots.txt | Regeln einschließlich Crawl-delay werden vor Inhaltsabrufen geprüft; unerwartete robots-Antworten führen zum Abbruch |
| Abstand | Mindestens 3 Sekunden zwischen Anfragen, in der Stadtkonfiguration anpassbar |
| Fehler | 429/5xx: wachsende Wartezeiten 60 s und 180 s, `Retry-After` als Sekundenwert oder HTTP-Datum |
| Lange Server-Pause | Über sechs Stunden: Lauf abbrechen statt die geforderte Pause zu verkürzen; `Retry-After` wird auch für Folgeläufe gespeichert |
| Sperre / Anmeldung | 401/403, ausgeschöpfte 429-Wiederholungen oder Login-Weiterleitung: sofort stoppen, keine Umgehung |
| Weiterleitungen | Inhalts-Weiterleitungen werden einzeln auf Host, Login, robots und Budget geprüft; Hostwechsel und robots-Weiterleitungen werden nicht ungeprüft verfolgt |
| Abkühlzeit | Nach Sperre/Nichterreichbarkeit 10 Minuten, bei weiteren Fehlschlägen verdoppelt bis 6 Stunden; längere Server-Vorgaben haben Vorrang |
| Budget / Fortschritt | `--max-anfragen` zählt auch robots und Weiterleitungen; gespeicherter Fortschritt bleibt erhalten |
| Konsistenz | Bauleit-Listenseite, Detail-Aufgaben und Cursor werden atomar mit verschachtelbaren SQLite-Savepoints gespeichert |
| Keine Such-Liveabrufe | Weboberfläche und API lesen nur lokale Daten |

Die Kölner OParl-robots.txt enthält keine Gruppe für unbekannte Clients; für einige namentlich genannte Bots gelten 20 Sekunden Crawl-delay. Unsere eigene Kennung nutzt mindestens drei Sekunden, sofern keine strengere passende Regel gilt. Die geprüfte NRW-robots.txt enthielt keine Einschränkungen. Beides ersetzt **keine** Abstimmung mit dem Betreiber.

## Grenzen und Datenqualität

- **Kein erfolgreicher End-to-End-Live-Crawl in dieser Entwicklungsumgebung:** Öffentliche Bauleit-JSON-Daten und eine UUID-Detailseite wurden zur Recherche erfolgreich gelesen; Adapter, Crawls und Fehlerfälle sind gegen Fixtures/Mock-HTTP getestet. Direkte Netzwerkabrufe aus der Sandbox waren nicht zuverlässig möglich. Auch Kölns Ratsinformation meldete zeitweise „Server ist nicht verfügbar“. `probe` und ein kleiner Lauf auf dem eigenen Rechner bleiben der Praxistest.
- Das Projekt [mandari](https://github.com/mandariOSS/mandari/issues/348) berichtet seit September 2026 über nicht erreichbare Kölner Hosts und vermutet eine IP-Sperre; **keine öffentlich bestätigte Aussage der Stadt**. Keine Ausweich-Hosts oder andere Umgehung.
- **Ortsbezug ist eine Heuristik:** OParl aus Betreff/Gremiennamen, Bauleitplanung aus Titel/öffentlicher Beschreibung. Die Herkunft wird im API-Ergebnis angezeigt. Kein Polygonabgleich und keine garantierte Zuordnung zur tatsächlichen Planfläche; auch andere im Text genannte Orte können Treffer auslösen. Kontaktangaben aus Detailseiten werden nicht als Lage übernommen.
- **PLZ-Zuordnung:** 9 Bezirke / 86 Stadtteile sind hinterlegt. Nur 5 der 46 Kölner PLZ haben eine ungefähre Stadtteil-Zuordnung; die übrigen suchen nur ausdrückliche PLZ-Nennungen. PLZ- und Stadtteilgrenzen sind nicht identisch.
- **„Geplant/offen“ priorisiert**, filtert aber nicht ausschließlich offene Vorgänge. Eine beendete Beteiligung bedeutet nicht, dass das Bauvorhaben abgeschlossen ist. Fristen werden einschließlich gelieferter Uhrzeiten/Zeitzonen geprüft und in Europe/Berlin dargestellt; maßgeblich bleibt die Originalseite.
- **Zusammenfassungen sind regelbasiert.** Bei Bauleitplanung werden Original-Sätze zu Ziel, Umfang und Zeitplan ausgewählt. Keine unabhängige Bestätigung von Investorenankündigungen, kein LLM und kein PDF-Volltext.
- RIS und Bauleitplanung können dasselbe Vorhaben als unterschiedliche Vorgänge führen. **Keine automatische Zusammenführung** und keine vollständige Historie vor dem ersten lokalen Abruf.
- **Noch nicht umgesetzt:** Bürgerbeteiligung `meinungfuer.koeln`, PDF-Volltext und optionale Sprachmodell-Zusammenfassungen. Recherche: [`docs/recherche.md`](docs/recherche.md).

## Architektur

```text
config/cities/*.toml
       │
       ├── OParl-Adapter + Crawler ───────────┐
       └── Bauleitplanung-Adapter + Crawler ─┤
                   über abruf.py            ▼
            robots / Budget / Backoff     SQLite
                                            │
                               Suche + quelleneigene Auswertung
                                            │
                                   CLI / FastAPI / Web
```

| Modul | Aufgabe |
|---|---|
| `config.py`, `geo.py`, `normalize.py` | Stadtkonfiguration, Orts-Heuristiken, Normalisierung |
| `nlq.py` | Regelbasiertes Fragenverständnis |
| `abruf.py` | Höflicher JSON-/HTML-Abruf und manuell geprüfte Weiterleitungen |
| `oparl.py`, `crawler.py` | OParl-Abbildung, Crawler-Registry, gemeinsamer Cooldown |
| `bauleitplanung.py` | Öffentliche Suche, Scope-Prüfung, Dokumentlinks, zweistufiger Crawl |
| `speicher.py` | Upserts, additive Schema-Migration, atomare Seiten, persistente Warteschlange |
| `suche.py` | Gemeinsame Filter/Ranking, quellengerechte Links und Ergebnisobjekte |
| `auswertung.py`, `plan_auswertung.py` | Rats-Ergebnisse bzw. Beteiligungsstatus, Zusammenfassungen, Verlauf |
| `demo.py`, `fixtures/` | Offline-Demo mit getrennt gekennzeichneten Quellen |
| `api.py`, `cli.py`, `web/` | API, CLI und responsive Weboberfläche |

### Datenmodell

- `vorgang`: gemeinsames Suchobjekt für Ratsvorlagen und Bauleitverfahren.
- `beratung`, `sitzung`, `tagesordnungspunkt`, `gremium`: überlieferte OParl-Beratungskette.
- `dokument`, `ortsbezug`, `vorgang_beziehung`: Dokumentverweise, Herkunft der Ortsangaben und OParl-Beziehungen.
- `planverfahren`: aktueller öffentlicher Stand mit Phase, Zeitraum, Beschreibung, letzter Sichtung und Listungs-/Detailstatus.
- `planstand`: Inhalts-Snapshots nur bei Änderungen; auch A → B → A bleibt im Verlauf sichtbar. Abrufdatum ist **nicht** amtliches Änderungsdatum.
- `crawl_stand`, `crawl_aufgabe`, `crawl_lauf`, `quelle`: Cursor/Zyklusmarker, wartende Detailabrufe, Laufprotokoll und Quellenstatus/Server-Pause.

Bestehende SQLite-Dateien werden beim Öffnen additiv erweitert; vorhandene Ratsdaten bleiben erhalten. OParl-`deleted=true` wird weich markiert. Bauleitverfahren werden bei fehlender Listung **nicht gelöscht**. Dokumentlinks werden erst nach einer gültigen öffentlichen Detailseite ersetzt.

## Weitere Städte und Quellen

1. **Stadt mit OParl:** `config/cities/<stadt>.toml` nach dem Vorbild Kölns anlegen; Bezirke, Stadtteile, PLZ, Themen und `[[quellen]]` ergänzen. `python -m behoerdenmaster --stadt <stadt> probe --quelle <quelle>`.
2. **Weitere Kommune auf derselben demosPlan-Plattform:** Quelle mit `typ="bauleitplanung"`, öffentlicher Such-URL samt `orgaSlug`, erwarteter `organisation` und `web_vorlage` konfigurieren. Öffentliches Schema und Detail-HTML müssen vorher geprüft werden – kein pauschales Versprechen für alle Installationen.
3. **Anderer Quelltyp:** Adapter/Crawl-Funktion in `CRAWLER` registrieren, Daten in das gemeinsame Modell abbilden und gegebenenfalls eigene Auswertung/DTO ergänzen. Keine fremde Quelle erhält automatisch OParl-Links oder dessen Lizenz.

## API

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/status` | Quellen, Bestand, letzte Läufe und Server-Pausen |
| POST | `/api/frage` | `{"frage":"…","stadt":"koeln","limit":10,"offset":0}` |
| GET | `/api/plz/{plz}?thema=bauen,verkehr&stadt=koeln` | PLZ-Suche |
| GET | `/api/vorgang?id=<URL-kodierte-Quell-ID>&stadt=koeln` | Detail mit Verlauf/Dokumenten; auf die gewählte Stadt beschränkt |

`links.original` / `original_text` zeigen die richtige Quellansicht. OParl-spezifische Linkfelder bleiben erhalten, sind bei Bauleitplanung aber `null`. Bauleittreffer haben zusätzlich `beteiligung` und quellspezifische `hinweise`; `verlauf[].typ` trennt Beteiligungsdaten von Abrufbeobachtungen. Interaktive API-Dokumentation: `/docs`.

Für Netzwerk-/Preview-Zugriff: `python -m behoerdenmaster serve --host 0.0.0.0 --port 8000`. Der Browser spricht dieselbe Anwendung über relative `/api`-URLs an; kein browserseitiger Zugriff auf ein Sandbox-`localhost`.

## Tests

```bash
pytest -q
python -m pyflakes behoerdenmaster tests
```

Offline getestet werden Fragen/Orte, beide Quelladapter, JSON-/HTML-Schema, Organisationsfilter, sichere Links/Weiterleitungen, robots, Backoff/HTTP-Datum, Budget, Pagination/Fortsetzung, atomare Checkpoints, KeyboardInterrupt, Cooldown über Prozessgrenzen, Schema-Migration, fehlende Verfahren, Änderungs-Snapshots, Demo-/Echtdaten-Trennung, Suche, API und CLI. GitHub Actions führt die Python-Tests und eine JavaScript-Syntaxprüfung aus; **keine Live-Behördenabfragen** in der CI.

## Lizenz und Datenschutz

- Im Repository ist derzeit **keine separate Code-Lizenz festgelegt**. Vor einer Weiterverteilung eine passende Lizenzentscheidung treffen. mandari (AGPL-3.0) und demosPlan (EUPL-1.2) wurden nur recherchiert; kein Code übernommen.
- OParl-Köln-Daten: Datenlizenz Deutschland – Zero – Version 2.0. **Diese Lizenz gilt nicht automatisch für Bauleitplanung Online.** Deren öffentliche Informationen und Dokumente können eigenen Nutzungsbedingungen unterliegen; keine pauschale offene Datenlizenz verifiziert.
- Keine privaten Stellungnahmen oder Anmeldebereiche. Öffentliche Beschreibungen und Ratsdokumente können trotzdem personenbezogene Angaben enthalten. Vor öffentlicher Bereitstellung Datenschutz, Aufbewahrung und Löschverfahren prüfen.

## Nächste Schritte

1. Abrufgrenzen und Nutzungsbedingungen mit den Betreibern abstimmen; beide Quellen auf einem geeigneten Rechner in kleinen Etappen live prüfen.
2. Bürgerbeteiligung Köln (`meinungfuer.koeln`) ergänzen.
3. PDF-Volltext mit Quellenbezug und optionalen Sprachmodell-Zusammenfassungen.
4. PLZ-Zuordnung für die übrigen 41 Kölner PLZ ergänzen und mögliche Verknüpfungen zwischen RIS und Bauleitplanung prüfen.
