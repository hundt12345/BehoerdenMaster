# BehoerdenMaster

Kommunale Vorgänge durchsuchen und nachvollziehbar zusammenfassen – zuerst für **Köln**, erweiterbar um weitere Städte und Quellen.

## Funktionen

- **Fragen in natürlicher Sprache**, etwa „Welche Bauvorhaben sind in Köln Ehrenfeld geplant?“: regelbasiertes Erkennen von Stadtteilen, Bezirken, Themen, Zeiträumen und „geplant/offen“.
- **PLZ-Suche**, optional mit Themenfilter.
- **Zwei Kölner Quellen in einer Suche:** Ratsinformation **OParl** und **Bauleitplanung Online NRW**.
- **Zwei RSS-Hinweisquellen (ohne Suche):** Sitzungstermine der Ratsinformation (SessionNet-RSS) und Pressemeldungen der Stadt. Sie landen als Hinweise mit Titel, Datum, Link und Kurztext in eigener Tabelle, nicht als Vorgänge.
- **Datenquellen und Ladestand:** eingeklappter Bereich unten auf der Startseite mit einer Grafik (Dokumentverweise je Quelle und Rolle) und einer Zeile je Quelle (Zustand, letzter Erfolg, Bestand). Die Daten kommen rein aus der lokalen Datenbank, ohne Behördenabruf; der ausführliche Ladepfad bleibt im JSON-Endpunkt `/api/ladebericht`.
- **Pro Vorgang:** Zusammenfassung, Status, Original-Link, Dokumentlinks und ein quellenabhängiges **Verlaufsprotokoll**:
  - OParl: Beratungsfolge mit Gremium, Sitzung, TOP und überliefertem Ergebnis.
  - Bauleitplanung: Beteiligungszeitraum, Phase und **lokal beobachtete Inhaltsänderungen**. Keine erfundene Ratsberatung und keine Behauptung einer vollständigen amtlichen Historie.
- Weboberfläche, FastAPI und CLI lesen ausschließlich die lokale SQLite-Datenbank. **Suchanfragen lösen keine Behördenabrufe aus.**

## Auf Render veröffentlichen

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/hundt12345/BehoerdenMaster/tree/arena/e6358663-behoerdenmaster)

Die [`render.yaml`](render.yaml) konfiguriert einen Python-3.13-Web-Service in Frankfurt im **kostenlosen Tarif (Render Free)**. Free hat keine persistente Disk: die SQLite-Datei liegt in `/tmp` und wird nach jedem Neustart vom Hintergrundabruf neu gefüllt. Damit dieser Erstabruf klein und höflich bleibt, begrenzt `BEHOERDEN_SYNC_SEIT` ihn auf ein **rollierendes 90-Tage-Fenster** statt auf den Wahlperiodenbeginn. Der Dienst schläft nach Inaktivität ein; der erste Aufruf danach dauert etwas länger. Für dauerhafte Daten siehe [docs/render.md](docs/render.md). Der Button verwendet den aktuellen PR-Branch; nach dessen Merge kann auch `main` ausgewählt werden.

Der Blueprint ruft **das Ratsinformationssystem Köln (OParl) automatisch ab**. Abrufe sind **standardmäßig aktiv**; als Kontaktadresse dient `BEHOERDEN_KONTAKT` – und solange die fehlt, die im Repository hinterlegte, erreichbare **Projekt-Kontaktadresse** (`behoerdenmaster/config.py`: `PROJEKT_KONTAKT`). Ein Deployment füllt seinen Bestand also auch ohne eigene Umgebungswerte, statt wegen einer fehlenden Einstellung dauerhaft leer zu bleiben; `BEHOERDEN_SYNC_ENABLED=false` schaltet bewusst ab, ein leer gesetztes `BEHOERDEN_KONTAKT` verzichtet bewusst auf die Voreinstellung. Beispieldaten sind ausgeschaltet. Suchanfragen lösen weiterhin nie einen Behördenabruf aus. [Schritt-für-Schritt-Anleitung, Einstellungen, Live-Probe und Sicherung](docs/render.md); bei einer leeren Startseite der Abschnitt [Fehlerbehebung](docs/render.md#fehlerbehebung).

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
python -m behoerdenmaster ladebericht   # was aus welcher Quelle geladen wurde
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

Jeder Abruf nennt eine erreichbare Kontaktadresse im User-Agent. Eigene Adresse setzen:

```bash
export BEHOERDEN_KONTAKT="it@ihre-organisation.example"   # durch die eigene Adresse ersetzen
```

Ist `BEHOERDEN_KONTAKT` nicht gesetzt, gilt die im Repository hinterlegte Projekt-Kontaktadresse (`PROJEKT_KONTAKT` in `behoerdenmaster/config.py`). Sie steht bewusst öffentlich im Code, damit auch ein Deployment ohne eigene Env-Werte identifizierbar abruft; eigene Instanzen sollten sie ersetzen. Ein **leer** gesetzter Wert verzichtet bewusst darauf – automatische Abrufe bleiben dann aus und werden in `/api/status` und auf der Startseite erklärt. Dieselbe Auflösung gilt für die CLI: `--kontakt` bzw. `BEHOERDEN_KONTAKT` haben Vorrang, sonst gilt die Projektadresse; die Verwendung wird vor dem Lauf angezeigt.

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

**OParl ist die Standardquelle**, wenn `--quelle` fehlt, und die Quelle, die der Render-Blueprint automatisch abruft. Vor einem größeren Erstabruf die zulässigen Grenzen mit der Stadt abstimmen (Kontakt aus dem OParl-System: `12-session@stadt-koeln.de`).

Der Abruf folgt der Reihenfolge **Gremien → Vorlagen → Sitzungen**. Die Vorlagen stehen bewusst vor den Sitzungen, denn nur sie sind die durchsuchbaren Vorgänge: Bei einem knappen Anfragebudget erzeugt ein Lauf sonst unter Umständen Sitzungsdaten, aber **keine einzigen Treffer** für die Startseite. Der Erstabruf lässt sich über `--seit` bzw. `BEHOERDEN_SYNC_SEIT` begrenzen; eine Tagesanzahl wie `90` ist ein rollierendes Fenster und veraltet nicht.

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

- **Schnittstelle am 11.10.2026 wieder erreichbar, aber kein Live-Abruf aus Sandbox oder Render-Netz bestätigt:** `robots.txt`, `/oparl/system`, `/oparl/bodies` sowie Gremien-, Vorlagen- und Sitzungslisten mit `modified_since` und realem `links.next` wurden geprüft und antworten. Die robots.txt der Stadt hat keine Gruppe für unbekannte Clients, `BehoerdenMaster` darf also abrufen und unterliegt keinem `Crawl-delay`. Der vollständige Abrufpfad (robots → System → Body → Listen → SQLite → Suche → API) ist mit den dabei aufgezeichneten **Originalantworten** als End-to-End-Test hinterlegt: [`fixtures/oparl/koeln/live_2026-10-11`](fixtures/oparl/koeln/live_2026-10-11/README.md). Aus der Entwicklungs-Sandbox sind ausgehende Abrufe zu den Behörden-Hosts jedoch gesperrt, und ob die Stadt Abrufe aus einer Rechenzentrums-IP zulässt, ist ungeprüft. `probe` und ein kleiner Lauf auf dem eigenen Rechner bzw. in der Render-Shell bleiben der Praxistest.
- **Freitextsuche nutzt Wortstämme, kein vollständiges Stemming.** Eine Frageform wie „Abendmärkten“ findet den Titel „Abendmärkte“, weil höchstens eine deutsche Endung entfernt wird und der Stamm mindestens fünf Zeichen behält – Kurzwörter wie „Rat“ oder „Bau“ werden deshalb nicht zu Wildcards. Umlaut-Plurale („Bebauungspläne“ zu „Bebauungsplan“) und Synonyme werden **nicht** erkannt.
- Das Projekt [mandari](https://github.com/mandariOSS/mandari/issues/348) berichtete seit September 2026 über nicht erreichbare Kölner Hosts und vermutete eine IP-Sperre; **keine öffentlich bestätigte Aussage der Stadt**. Keine Ausweich-Hosts oder andere Umgehung.
- **Ortsbezug ist eine Heuristik:** OParl aus Betreff/Gremiennamen, Bauleitplanung aus Titel/öffentlicher Beschreibung. Die Herkunft wird im API-Ergebnis angezeigt. Kein Polygonabgleich und keine garantierte Zuordnung zur tatsächlichen Planfläche; auch andere im Text genannte Orte können Treffer auslösen. Kontaktangaben aus Detailseiten werden nicht als Lage übernommen.
- **PLZ-Zuordnung:** 9 Bezirke / 86 Stadtteile sind hinterlegt. Nur 5 der 46 Kölner PLZ haben eine ungefähre Stadtteil-Zuordnung; die übrigen suchen nur ausdrückliche PLZ-Nennungen. PLZ- und Stadtteilgrenzen sind nicht identisch.
- **„Geplant/offen“ priorisiert**, filtert aber nicht ausschließlich offene Vorgänge. Eine beendete Beteiligung bedeutet nicht, dass das Bauvorhaben abgeschlossen ist. Fristen werden einschließlich gelieferter Uhrzeiten/Zeitzonen geprüft und in Europe/Berlin dargestellt; maßgeblich bleibt die Originalseite.
- **Zusammenfassungen sind regelbasiert.** Bei Bauleitplanung werden Original-Sätze zu Ziel, Umfang und Zeitplan ausgewählt. Keine unabhängige Bestätigung von Investorenankündigungen, kein LLM und kein PDF-Volltext.
- RIS und Bauleitplanung können dasselbe Vorhaben als unterschiedliche Vorgänge führen. **Keine automatische Zusammenführung** und keine vollständige Historie vor dem ersten lokalen Abruf.
- **Noch nicht umgesetzt:** Bürgerbeteiligung `meinungfuer.koeln`, Bekanntmachungen/Amtsblatt, Offene Daten Köln, Statistik, PDF-Volltext und optionale Sprachmodell-Zusammenfassungen. **Geoportal-Dienste (Bebauungsplan-Übersicht, Verkehr, Baustellen) sind laut `robots.txt` für alle Crawler gesperrt** und deshalb nicht angebunden. Stand und Begründung: [`docs/quellen-koeln.md`](docs/quellen-koeln.md#umsetzungsstand). Recherche: [`docs/recherche.md`](docs/recherche.md).

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
| `betrieb.py` | Startinitialisierung, robuste Umgebungsauswertung mit Warnungen, Hintergrundabruf |
| `suche.py` | Gemeinsame Filter/Ranking, quellengerechte Links und Ergebnisobjekte |
| `auswertung.py`, `plan_auswertung.py` | Rats-Ergebnisse bzw. Beteiligungsstatus, Zusammenfassungen, Verlauf |
| `ladebericht.py` | Ladeübersicht: Bestand je Quelle/Rolle, Ladepfad, Listenstand, Abrufprotokoll (nur lesend) |
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
| GET / HEAD | `/healthz` | Lokaler Datenbank-Healthcheck (200/503), keine Behördenabfrage |
| GET | `/api/status` | Quellen, Bestand, letzte Läufe, Server-Pausen und Hintergrundabruf-Status. Zusätzlich `bestand_leer` und `hinweise`: Bei einem leeren Bestand wird der Grund samt zuständiger Einstellung erklärt, statt nur null Treffer zu liefern. `betrieb.warnungen` meldet Konfigurationsprobleme, die den Dienst bewusst nicht stoppen, `betrieb.kontakt_hinterlegt` ob eine Kontaktadresse vorliegt – die Adresse selbst wird nie ausgegeben. |
| POST | `/api/frage` | `{"frage":"…","stadt":"koeln","limit":10,"offset":0}` |
| GET | `/api/ladebericht?stadt=koeln&laeufe=8` | Ladeübersicht: Bestand, Dokumentverweise je Quelle und Rolle, Ladepfad, Listenstand, nächster Abruf, Abrufprotokoll. Nur lokale Daten, kein Behördenabruf. Dieselben Zahlen liefert `python -m behoerdenmaster ladebericht [--json]`. |
| GET | `/api/plz/{plz}?thema=bauen,verkehr&stadt=koeln` | PLZ-Suche |
| GET | `/api/vorgang?id=<URL-kodierte-Quell-ID>&stadt=koeln` | Detail mit Verlauf/Dokumenten; auf die gewählte Stadt beschränkt |

`links.original` / `original_text` zeigen die richtige Quellansicht. OParl-spezifische Linkfelder bleiben erhalten, sind bei Bauleitplanung aber `null`. Bauleittreffer haben zusätzlich `beteiligung` und quellspezifische `hinweise`; `verlauf[].typ` trennt Beteiligungsdaten von Abrufbeobachtungen. Interaktive API-Dokumentation: `/docs`.

Für Netzwerk-/Preview-Zugriff: `python -m behoerdenmaster serve --host 0.0.0.0 --port 8000`. Ohne `--port` gilt `$PORT` (Render) oder lokal 8000. Der Browser spricht dieselbe Anwendung über relative `/api`-URLs an; kein browserseitiger Zugriff auf ein Sandbox-`localhost`. Ein Web-Lifespan initialisiert das Schema und startet die Hintergrund-Aktualisierung, sofern `BEHOERDEN_SYNC_ENABLED` nicht auf `false` gesetzt ist; als Kontaktadresse dient `BEHOERDEN_KONTAKT`, sonst die im Repository hinterlegte `PROJEKT_KONTAKT`. Ein leer gesetztes `BEHOERDEN_KONTAKT` (bewusster Verzicht) lässt den Abruf aus; der Dienst startet trotzdem und meldet das in `betrieb.warnungen`. Ungültige Einzelwerte fallen auf ihren Standard zurück, statt den Start abzubrechen. Es gibt keinen öffentlichen Crawl-/Admin-Endpunkt; manuelle Abrufe laufen über die CLI und teilen sich mit dem Hintergrunddienst eine Dateisperre.

## Datenbank sichern

```bash
python -m behoerdenmaster backup data/backups/behoerdenmaster-sicherung.sqlite
```

Die SQLite-Backup-API erzeugt eine geprüfte, eigenständige Sicherung einschließlich WAL. Vorhandene Zieldateien werden nicht überschrieben. Eine Kopie auf derselben Disk allein ist noch kein Backup gegen Disk-Verlust; unabhängig aufbewahren. Keine aktive SQLite-Datei allein mit `cp` sichern und keine Wiederherstellung bei laufendem Web-/Sync-Prozess durchführen. Weitere Hinweise: [Render-Anleitung](docs/render.md#5-demo-entfernen-daten-sichern-redeploys).

## Tests

```bash
pytest -q
python -m pyflakes behoerdenmaster tests
```

Offline getestet werden die Ladeübersicht (Dokumentverweise je Quelle und Rolle, Ladepfad, Listenstand, Abrufprotokoll, Abkühlzeit, leerer Bestand, API- und CLI-Ausgabe, kein Netzaufbau), Fragen/Orte, beide Quelladapter, JSON-/HTML-Schema, Organisationsfilter, sichere Links/Weiterleitungen, robots, Backoff/HTTP-Datum, Budget, Pagination/Fortsetzung, atomare Checkpoints, KeyboardInterrupt, Cooldown über Prozessgrenzen, Schema-Migration, fehlende Verfahren, Änderungs-Snapshots, Demo-/Echtdaten-Trennung, Suche, API und CLI sowie Render-Blueprint, Startup/Healthcheck, Einmal-Seeding, Neustart-Fälligkeit, Hintergrundabruf/Shutdown, Prozesssperre und WAL-Sicherung. Dazu die **Umgebungsvoreinstellung**: Abrufe sind ohne gesetzte Variablen aktiv, die Projekt-Kontaktadresse ersetzt ein fehlendes `BEHOERDEN_KONTAKT`, ein leer gesetzter Wert verzichtet bewusst darauf, und keine der Adressen verlässt den Server.

Hinzu kommen drei Prüfungen gegen die veröffentlichte Instanz: der **vollständige Abrufpfad gegen die Originalantworten vom 11.10.2026** (robots → System → Body → Listen mit echtem `links.next` → SQLite → Suche → API, einschließlich geschlossener Beratungsfolge mit überliefertem Ergebnis), die **Abrufreihenfolge bei knappem Budget** (Vorlagen vor Sitzungen, damit ein Teilabruf sichtbare Treffer hinterlässt) sowie **Diagnose und Robustheit eines leeren Bestands** (erklärter Leerstand in API und Oberfläche, warnende statt abstürzende Umgebungsauswertung, rollierendes Erstabruf-Fenster). GitHub Actions führt die Python-Tests und eine JavaScript-Syntaxprüfung aus; **keine Live-Behördenabfragen** in der CI.

## Lizenz und Datenschutz

- Im Repository ist derzeit **keine separate Code-Lizenz festgelegt**. Vor einer Weiterverteilung eine passende Lizenzentscheidung treffen. mandari (AGPL-3.0) und demosPlan (EUPL-1.2) wurden nur recherchiert; kein Code übernommen.
- OParl-Köln-Daten: Datenlizenz Deutschland – Zero – Version 2.0. **Diese Lizenz gilt nicht automatisch für Bauleitplanung Online.** Deren öffentliche Informationen und Dokumente können eigenen Nutzungsbedingungen unterliegen; keine pauschale offene Datenlizenz verifiziert.
- Keine privaten Stellungnahmen oder Anmeldebereiche. Öffentliche Beschreibungen und Ratsdokumente können trotzdem personenbezogene Angaben enthalten. Vor öffentlicher Bereitstellung Datenschutz, Aufbewahrung und Löschverfahren prüfen.

## Nächste Schritte

1. Abrufgrenzen und Nutzungsbedingungen mit den Betreibern abstimmen; beide Quellen auf einem geeigneten Rechner in kleinen Etappen live prüfen.
2. Weitere Kölner Quellen in der Reihenfolge aus [`docs/quellen-koeln.md`](docs/quellen-koeln.md) anbinden – vorgeschlagen zuerst der SessionNet-RSS-Feed und die öffentlichen Bekanntmachungen (kleiner Aufwand, amtlicher Statuswechsel), danach die Kölner Bebauungsplan-Übersicht (Geometrie statt Orts-Heuristik).
3. Bürgerbeteiligung Köln (`meinungfuer.koeln`) ergänzen.
4. PDF-Volltext mit Quellenbezug und optionalen Sprachmodell-Zusammenfassungen.
5. PLZ-Zuordnung für die übrigen 41 Kölner PLZ ergänzen und mögliche Verknüpfungen zwischen RIS und Bauleitplanung prüfen. Eigenständig zusammengestellte Themenüberblicke mit Quellenangabe: [`docs/presse-koeln.md`](docs/presse-koeln.md).
