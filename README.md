# BehoerdenMaster

Kommunale Vorgänge aus Behördeninformationssystemen durchsuchen und verständlich zusammenfassen – zuerst für **Köln**, mit Erweiterbarkeit für weitere Städte und Quellen.

## Was die Anwendung kann

- **Fragen in natürlicher Sprache**, z. B. „Welche Bauvorhaben sind in Köln Ehrenfeld geplant?“. Erkannt werden Stadt, Stadtteil bzw. Stadtbezirk, Themen (Bauvorhaben, Verkehr, Schulen, Grün), Zeiträume („seit 2024“, „2023“) und die Absicht „geplant/offen“.
- **PLZ-Suche**: alle Vorgänge zu einer Postleitzahl, optional mit Themenfilter.
- **Je Vorgang**: Betreff, Art, Nummer, Datum, Status, Kurzzusammenfassung, **Verlaufsprotokoll** (Beratungsfolge mit Gremium, Sitzung, Tagesordnungspunkt und Ergebnis), **Dokumente** und **Links** zur Originalansicht im Ratsinformationssystem.
- **Erweiterbar**: Neue Städte entstehen durch eine Konfigurationsdatei unter `config/cities/`. Neue Quelltypen werden über eine Registry im Crawler ergänzt.

## Ehrliche Einordnung (Stand dieser Version)

- **Datenquelle Köln:** die OParl-Schnittstelle des Ratsinformationssystems (JSON, Datenlizenz Deutschland – Zero – 2.0). Der Crawler speichert die Daten lokal. **Nutzeranfragen gehen nie an die Stadt.**
- **Die Stadt begrenzt Abrufe.** Das Projekt mandari berichtet, dass Köln seine Server seit September 2026 nicht mehr erreichte, vermutlich wegen zu vieler Anfragen (Issues #89 und #348 im GitHub-Repo mandariOSS/mandari; die Stadt hat dazu nicht öffentlich Stellung genommen). Der Crawler ist daher bewusst zurückhaltend: Standard eine Anfrage alle 3 Sekunden, ein Anfragebudget pro Lauf und Abbruch bei Sperre. **Vor dem ersten vollständigen Abruf sollte die Stadt kontaktiert werden** (Kontakt laut OParl-System: `12-session@stadt-koeln.de`).
- **Ein Live-Abruf ist in der Entwicklungsumgebung nicht erfolgreich getestet worden.** Während der Entwicklung meldeten die Server der Stadt „Server ist nicht verfügbar“. Getestet wurde mit Auszügen echter Antworten und einer simulierten Schnittstelle. Der erste Abruf auf Ihrem Rechner ist deshalb der eigentliche Praxistest (siehe `probe`).
- **Ortsbezug:** Die OParl-Daten enthalten für Köln in den geprüften Antworten keine verlässlichen Ortsangaben an den Vorlagen (die Ortsobjekte der ersten Seite sind leer). Stadtteil und Bezirk werden deshalb aus Betreff und Gremiennamen abgeleitet. Das ist eine Heuristik; jede Zuordnung zeigt ihre Herkunft (Betreff oder Gremium).
- **PLZ-Zuordnung:** Für 5 der 46 Kölner PLZ ist eine Näherung hinterlegt (Quelle onlinestreet.de, Datenbasis Destatis/BZSt und OpenStreetMap). Die übrigen sind in `config/cities/koeln.toml` als offen markiert. Vorgänge mit ausdrücklicher PLZ-Nennung werden immer gefunden.
- **Zusammenfassungen sind regelbasiert** und bestehen nur aus erfassten Daten. Ein Sprachmodell ist nicht eingebunden. Ein Hook dafür ist vorgesehen (siehe „Nächste Schritte“).
- **Noch nicht umgesetzt:** Bauleitplanung Online (Beteiligungsverfahren in NRW), die Bürgerbeteiligung der Stadt Köln (meinungfuer.koeln) und der Volltext der PDF-Dokumente. Die Recherche dazu steht in [`docs/recherche.md`](docs/recherche.md).

## Schnellstart

Voraussetzungen: Python 3.11 oder neuer.

```bash
python3 -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest                            # Tests – ohne Netzzugang lauffähig
python -m behoerdenmaster demo    # Beispieldaten laden (Auszüge echter Vorlagen)
python -m behoerdenmaster frage "Welche Bauvorhaben sind in Köln Ehrenfeld geplant?"
python -m behoerdenmaster plz 50827
python -m behoerdenmaster serve   # Weboberfläche auf http://127.0.0.1:8000
```

Wird das Paket nicht aus diesem Repository heraus genutzt, zeigt `BEHOERDEN_CONFIG_DIR` auf das Verzeichnis mit den Stadtkonfigurationen. Die Datenbank liegt dann über `BEHOERDEN_DB`.

## Echte Daten abrufen (Köln)

```bash
export BEHOERDEN_KONTAKT="it@ihre-organisation.example"   # Pflicht: wird im User-Agent genannt

python -m behoerdenmaster probe                 # prüft die Erreichbarkeit, höchstens 5 Anfragen, speichert nichts
python -m behoerdenmaster crawl --seit 2025-11-01 --max-anfragen 300   # erster Lauf, in Etappen
python -m behoerdenmaster crawl --max-anfragen 300                    # setzt an derselben Stelle fort
python -m behoerdenmaster crawl                                       # Folgeläufe holen nur Änderungen
python -m behoerdenmaster status                                      # Stand, Bestand, letzte Läufe
```

`--seit` gilt nur für den ersten Lauf einer Quelle. Danach merkt sich der Crawler den Stand. `--max-anfragen` begrenzt einen Lauf, damit große Abrufe in kleinen Schritten erfolgen, wie es die Stadt voraussichtlich erwartet. Die Datenbank liegt standardmäßig unter `data/behoerdenmaster.sqlite` (per `--db` oder `BEHOERDEN_DB` änderbar).

### Verhalten gegenüber der Quelle

| Regel | Umsetzung |
|---|---|
| Erkennbarer Abrufer | User-Agent `BehoerdenMaster/0.1 (+Projekt-URL; Kontakt: …)`, Kontakt Pflicht |
| robots.txt | Vor jedem Host-Abruf nach RFC 9309 geprüft, inkl. Crawl-delay |
| Abstand | Mindestens 3 Sekunden zwischen Anfragen (`min_intervall_sekunden` in `koeln.toml`) |
| Fehler | 429/5xx: Wartezeiten 60 s und 180 s, `Retry-After` wird beachtet |
| Sperre | Bei 401/403 oder 429 nach den Wartezeiten bricht der Lauf sofort ab. Es gibt **keinen** Umgehungsversuch. |
| Abkühlzeit | Nach einer Sperre startet der nächste Lauf frühestens nach 10 Minuten, bei jedem weiteren Fehlschlag verdoppelt bis höchstens 6 Stunden |
| Budget | `--max-anfragen` begrenzt jeden Lauf. Der Fortschritt bleibt gespeichert. |
| Keine Live-Abrufe | Die Weboberfläche und die API lesen ausschließlich die lokale Datenbank |

Hinweis zu robots.txt: Die Datei von `buergerinfo.stadt-koeln.de` enthält keine Regel für unbekannte Clients und nennt für bekannte Bots einen Crawl-delay von 20 Sekunden. Für diese Anwendung gilt deshalb der eigene Mindestabstand von 3 Sekunden. Wenn die Stadt andere Werte nennt, sind sie in `koeln.toml` anzupassen.

## Architektur

```
config/cities/koeln.toml ──┐
                           ▼
 OParl-Quelle (oparl.py) ──► Crawler (crawler.py) ──► SQLite (speicher.py)
   über abruf.py:                                         │
   robots, Drosselung,                                    ▼
   Backoff, Budget          Suche (suche.py) + Auswertung (auswertung.py)
                                                          │
                                  CLI (cli.py) ───────────┴──► API (api.py) ──► Weboberfläche (web/index.html)
```

| Modul | Aufgabe |
|---|---|
| `config.py` | Lädt Städte aus `config/cities/*.toml` (Bezirke, Stadtteile, PLZ, Themen, Quellen) |
| `normalize.py` | Textnormalisierung (Umlaute, Satzzeichen) für Abgleich und Suche |
| `geo.py` | Ortsbezug aus Texten: Stadtteile, Bezirke, PLZ, mit Schutz vor Fehltreffern |
| `nlq.py` | Regelbasiertes Verstehen von Fragen (Ort, Thema, Zeitraum, „geplant“, Freitext) |
| `abruf.py` | Höflicher HTTP-Abruf: robots.txt, Drosselung, Backoff, Budget, keine Umgehung |
| `oparl.py` | OParl-Adapter: Paginierung über `links.next`, Abbildung der Objekte |
| `crawler.py` | Crawl-Läufe, Fortsetzung, Löschungen, Registry der Quelltypen |
| `speicher.py` | SQLite-Schema, idempotente Upserts, Crawl-Stand, Läufe |
| `suche.py` | Filter, Ranking, Verlauf und Dokumente je Treffer |
| `auswertung.py` | Ergebnisklassen, Status („anstehend“, „Beschluss gefasst“ …), Zusammenfassung |
| `demo.py` | Lädt die Beispieldaten aus `fixtures/` über denselben Verarbeitungspfad |
| `api.py`, `cli.py`, `web/` | HTTP-API, Kommandozeile und Weboberfläche |

### Datenmodell (Auszug)

- **Vorgang** (OParl „Paper“): Betreff, Nummer, Art, Datum, Ortsbezug, Suchtext.
- **Beratung** (Beratungsfolge): verbindet Vorgang, Sitzung, Tagesordnungspunkt und Gremium. Das ist das Verlaufsprotokoll.
- **Sitzung** (OParl „Meeting“) mit **Tagesordnungspunkten** (Ergebnis, Nummer) und Dokumenten (Einladung, Tagesordnung, Protokolle).
- **Gremium**, **Dokument** (Verweis auf die PDF der Stadt), **Ortsbezug** (Typ, Schlüssel, Herkunft).

Gelöschte Objekte (`deleted: true`) werden weich markiert. Kinder wie Beratungen, Dokumente und Ortsbezug werden beim Speichern des Elternobjekts ersetzt, sodass die Daten konsistent bleiben.

## Neue Stadt oder neue Quelle

1. **Stadt mit OParl-Schnittstelle:** `config/cities/<stadt>.toml` nach dem Vorbild von `koeln.toml` anlegen. Bezirke, Stadtteile, PLZ und Themen eintragen und einen Block `[[quellen]]` mit `typ = "oparl"` und `system_url` ergänzen. Danach `python -m behoerdenmaster --stadt <stadt> probe` ausführen.
2. **Anderer Quelltyp** (z. B. Bauleitplanung Online oder SessionNet-HTML): Adapter-Funktion im Stil von `crawle_oparl` schreiben, die Objekte in die Speicherzeilen abbildet, und sie in `CRAWLER` in `crawler.py` eintragen. Suche, API und Oberfläche bleiben unverändert.

## API

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/status` | Quellen, Bestand, letzte Läufe |
| POST | `/api/frage` | Body `{"frage": "…", "limit": 10, "offset": 0}` → Verständnis, Treffer, Hinweise |
| GET | `/api/plz/{plz}?thema=bauen,verkehr` | Vorgänge zu einer PLZ |
| GET | `/api/vorgang?id=<OParl-ID>` | Detail eines Vorgangs mit Verlauf und Dokumenten |

Die interaktive Dokumentation liegt unter `/docs`.

## Tests

```bash
pytest -q
```

Die Tests laufen ohne Netzzugang. Sie decken ab: Ortsbezug und Fragenverständnis, Abrufverhalten (Drosselung, Backoff, `Retry-After`, robots.txt, Budget), Abbildung der OParl-Objekte, Crawl-Läufe (Paginierung, Fortsetzung, Sperre und Abkühlzeit, Löschungen), Suche und Ranking, Status- und Zusammenfassungslogik, API und Konfiguration.

## Lizenz und Datenschutz

- Der Code steht unter der Lizenz dieses Repositorys. Das Projekt mandari (AGPL-3.0) diente nur als Recherchequelle. Es wurde kein Code übernommen.
- Die gespeicherten Ratsdaten (Vorlagen, Sitzungen, Dokumente) sind öffentlich. Bürgereingaben in Vorlagen können personenbezogene Angaben enthalten. Vor einer öffentlichen Bereitstellung sollte der Datenschutz geprüft und ggf. ein Löschverfahren vorgesehen werden.
- Die Beispieldaten in `fixtures/` sind Auszüge echter Antworten der Stadt Köln (Datenlizenz Deutschland – Zero – 2.0). Details: [`fixtures/oparl/koeln/README.md`](fixtures/oparl/koeln/README.md).

## Nächste Schritte

1. **Stadt Köln kontaktieren** und Abrufgrenzen vereinbaren, bevor ein vollständiger Abruf läuft.
2. **Ersten Abruf in Etappen** mit `--max-anfragen` durchführen und die Ergebnisse prüfen.
3. **Bauleitplanung Online** (`nw.bauleitplanung-online.de/plaene/koeln`) als zweite Quelle anbinden. Die robots.txt erlaubt den Abruf. Die Seite listet laufende Beteiligungsverfahren mit Zeitraum und Ort (z. B. „254. Änderung des FNP … in Köln-Ehrenfeld“).
4. **Bürgerbeteiligung der Stadt Köln** (`meinungfuer.koeln`) anbinden.
5. **PDF-Volltext** (Begründungen, Beschlussvorlagen) für bessere Zusammenfassungen und Ortserkennung.
6. **PLZ-Zuordnung** für die übrigen 41 Kölner PLZ ergänzen (Quelle und Stand in der Konfiguration vermerken).
7. **Optional:** Sprachmodell-Zusammenfassung mit Quellenangaben, nur für bereits gespeicherte Texte.
