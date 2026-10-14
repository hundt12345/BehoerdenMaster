# Angebundene Städte

<!-- Erzeugt mit `python -m behoerdenmaster staedte --markdown > docs/staedte.md`. Nicht von Hand ändern; ein Test prüft, dass die Datei zur Konfiguration passt. -->

Alle Städte nutzen **dasselbe System**: dieselben Adapter, dieselbe Drossel je Host, robots.txt,
Budgets, Abkühlzeiten und dieselbe lokale Suche. Eine neue Stadt ist eine neue Datei in
`config/cities/`; die Ortsgliederung erzeugt `tools/stadt_aus_osm.py` aus dem OpenStreetMap-Auszug.

**Abrufmodus.** *Dauerhaft* abgerufene Städte hält der Hintergrunddienst laufend aktuell.
Städte *auf Abruf* sind vollständig angebunden, werden aber erst geladen, wenn jemand sie in
der Oberfläche auswählt oder in ihr sucht – das ist nur ein Vermerk in der lokalen Datenbank;
abgerufen wird ausschließlich im gedrosselten Hintergrundplan, nie während einer Suche.
Den aktuellen Ladezustand zeigen die Oberfläche (Bereich „Angebundene Städte“),
`GET /api/staedte` und `python -m behoerdenmaster staedte`.

| Stadt | Abruf | Quellen | Ortsgliederung | Prüfstand |
|---|---|---|---|---|
| **Köln** (Standard) | dauerhaft | Ratsinformation (OParl), Bauleitplanung Online NRW, Sitzungstermine (RSS), Pressemeldungen (RSS), Öffentliche Bekanntmachungen, Bürgerbeteiligung, Offene Daten (DKAN 2) | 9 Bezirke, 86 Stadtteile, 46 PLZ, 4981 Straßen | Originalantworten am 11.10.2026 geprüft und als Testdaten aufgezeichnet; Abruf aus dem Render-Netz noch nicht bestätigt |
| **Aachen** | auf Abruf | Ratsinformation (OParl) | 7 Bezirke, 10 PLZ (keine Stadtteile) | Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen |
| **Bonn** | auf Abruf | Ratsinformation (OParl) | 4 Bezirke, 51 Stadtteile, 17 PLZ, 2244 Straßen | Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen |
| **Düsseldorf** | auf Abruf | Ratsinformation (OParl) | 10 Bezirke, 50 Stadtteile, 37 PLZ, 2785 Straßen | Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen |
| **Münster** | auf Abruf | Ratsinformation (OParl) | 6 Bezirke, 22 Stadtteile, 13 PLZ, 1999 Straßen | Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen |
| **Wuppertal** | auf Abruf | Ratsinformation (OParl) | 10 Bezirke, 23 PLZ (keine Stadtteile) | Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen |

## Je Stadt

### Köln (`koeln`)

- Bundesland: Nordrhein-Westfalen · Amtlicher Gemeindeschlüssel: 05315000
- Abruf: dauerhaft – wird laufend aktualisiert
- Ortsgliederung: Bezirke, Stadtteile, PLZ und Straßen – Wikipedia „Liste der Stadtbezirke und Stadtteile Kölns“ (amtliche Nummerierung); PLZ und Straßen aus OSM-Auszug (ODbL 1.0)
- Quelle `koeln:ris` – Ratsinformationssystem Köln (OParl 1.1): `https://buergerinfo.stadt-koeln.de/oparl/system`
  - Prüfstand: Originalantworten am 11.10.2026 geprüft und als Testdaten aufgezeichnet; Abruf aus dem Render-Netz noch nicht bestätigt
  - Lizenz: Datenlizenz Deutschland – Zero – Version 2.0
- Quelle `koeln:bauleitplanung` – Bauleitplanung Online Köln: `https://nw.bauleitplanung-online.de/verfahren/suche/ajax?orgaSlug=koeln`
  - Prüfstand: Listenantwort am 09.10.2026 beobachtet; Live-Lauf nicht bestätigt, im Render-Blueprint bis zur Klärung ausgenommen
  - Lizenz: Öffentliche Verfahrensinformationen; Nutzungsbedingungen der Plattform beachten
- Quelle `koeln:ris-rss` – Sitzungstermine Ratsinformation (RSS): `https://buergerinfo.stadt-koeln.de/rssfeed.asp?id=1&filter=snvf`
  - Prüfstand: Endpunkt und robots.txt am 12.10.2026 geprüft
  - Lizenz: Datenlizenz Deutschland – Zero – Version 2.0 (laut docs/quellen-koeln.md, je Feed bestätigen)
- Quelle `koeln:presse-rss` – Pressemeldungen Stadt Köln (RSS): `https://www.stadt-koeln.de/externe-dienste/rss/pressemeldungen.xml`
  - Prüfstand: Endpunkt und robots.txt am 12.10.2026 geprüft
  - Lizenz: Datenlizenz Deutschland – Namensnennung – Version 2.0 (laut Datensatz "Pressemeldungen" auf offenedaten-koeln.de; der Feed selbst nennt keine Lizenz)
- Quelle `koeln:bekanntmachungen` – Öffentliche Bekanntmachungen Stadt Köln: `https://www.stadt-koeln.de/politik-und-verwaltung/bekanntmachungen/oeffentliche-bekanntmachungen-2026`
  - Prüfstand: Live-Probelauf aus einem Rechenzentrums-Netz am 12.10.2026 bestätigt (135 Bekanntmachungen)
  - Lizenz: Amtliche Bekanntmachung; keine offene Lizenz zugesichert – nur Titel, Datum und Link werden übernommen
- Quelle `koeln:beteiligung` – Bürgerbeteiligung Meinung für Köln: `https://meinungfuer.koeln/mitmachen/alle-projekte`
  - Prüfstand: Live-Probelauf aus einem Rechenzentrums-Netz am 12.10.2026 bestätigt (56 Projekte)
  - Lizenz: Keine offene Lizenz zugesichert; nur Titel, Statuszeile, Kurztext und Link der städtischen Projektseite
- Quelle `koeln:opendata` – Offene Daten Köln (Datensätze): `https://www.offenedaten-koeln.de`
  - Prüfstand: Offline gegen den DKAN-2-Vertrag getestet; Live-Lauf steht aus
  - Lizenz: Je Datensatz DL-DE-Zero-2.0 (ohne Namensnennung) oder DL-DE-BY-2.0 (mit Namensnennung); andere Lizenzen werden übersprungen

### Aachen (`aachen`)

- Bundesland: Nordrhein-Westfalen · Amtlicher Gemeindeschlüssel: 05334002
- Abruf: auf Abruf – wird erst nach Anforderung geladen
- Ortsgliederung: nur Stadtbezirke (keine Stadtteile im OSM-Auszug) – Stadtbezirke aus dem OSM-Auszug der OpenPLZ API (ODbL 1.0), erzeugt mit tools/stadt_aus_osm.py
- Quelle `aachen:ris` – Ratsinformation Aachen (OParl): `https://ratsinfo.aachen.de/bi/oparl/1.0/system.asp`
  - Prüfstand: Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen
  - Lizenz: Nicht geprüft – vor einer Veröffentlichung die Lizenzangabe des OParl-Systems bzw. der Stadt prüfen

### Bonn (`bonn`)

- Bundesland: Nordrhein-Westfalen · Amtlicher Gemeindeschlüssel: 05314000
- Abruf: auf Abruf – wird erst nach Anforderung geladen
- Ortsgliederung: Bezirke, Stadtteile, PLZ und Straßen – Stadtbezirke und Stadtteile aus dem OSM-Auszug der OpenPLZ API (ODbL 1.0), erzeugt mit tools/stadt_aus_osm.py
- Quelle `bonn:ris` – Ratsinformation Bonn (OParl): `https://www.bonn.sitzung-online.de/public/oparl/system`
  - Prüfstand: Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen
  - Lizenz: Nicht geprüft – vor einer Veröffentlichung die Lizenzangabe des OParl-Systems bzw. der Stadt prüfen

### Düsseldorf (`duesseldorf`)

- Bundesland: Nordrhein-Westfalen · Amtlicher Gemeindeschlüssel: 05111000
- Abruf: auf Abruf – wird erst nach Anforderung geladen
- Ortsgliederung: Bezirke, Stadtteile, PLZ und Straßen – Stadtbezirke und Stadtteile aus dem OSM-Auszug der OpenPLZ API (ODbL 1.0), erzeugt mit tools/stadt_aus_osm.py
- Quelle `duesseldorf:ris` – Ratsinformation Düsseldorf (OParl): `https://ris-oparl.itk-rheinland.de/Oparl/system`
  - Prüfstand: Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen
  - Lizenz: Nicht geprüft – vor einer Veröffentlichung die Lizenzangabe des OParl-Systems bzw. der Stadt prüfen

### Münster (`muenster`)

- Bundesland: Nordrhein-Westfalen · Amtlicher Gemeindeschlüssel: 05515000
- Abruf: auf Abruf – wird erst nach Anforderung geladen
- Ortsgliederung: Bezirke, Stadtteile, PLZ und Straßen – Stadtbezirke und Stadtteile aus dem OSM-Auszug der OpenPLZ API (ODbL 1.0), erzeugt mit tools/stadt_aus_osm.py
- Quelle `muenster:ris` – Ratsinformation Münster (OParl): `https://oparl.stadt-muenster.de/system`
  - Prüfstand: Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen
  - Lizenz: Nicht geprüft – vor einer Veröffentlichung die Lizenzangabe des OParl-Systems bzw. der Stadt prüfen

### Wuppertal (`wuppertal`)

- Bundesland: Nordrhein-Westfalen · Amtlicher Gemeindeschlüssel: 05124000
- Abruf: auf Abruf – wird erst nach Anforderung geladen
- Ortsgliederung: nur Stadtbezirke (keine Stadtteile im OSM-Auszug) – Stadtbezirke aus dem OSM-Auszug der OpenPLZ API (ODbL 1.0), erzeugt mit tools/stadt_aus_osm.py
- Quelle `wuppertal:ris` – Ratsinformation Wuppertal (OParl): `https://oparl.wuppertal.de/oparl/system`
  - Prüfstand: Endpunkt aus der OParl-Endpunktliste (Stand 14.10.2026); noch kein Abruf – vor dem ersten Lauf probe ausführen
  - Lizenz: Nicht geprüft – vor einer Veröffentlichung die Lizenzangabe des OParl-Systems bzw. der Stadt prüfen

## Neue Stadt anbinden

1. OParl-Endpunkt in der offiziellen Liste nachsehen (github.com/OParl/resources, `endpoints.yml`).
2. `config/cities/<stadt>.toml` nach dem Vorbild von `bonn.toml` anlegen: `[stadt]` mit `ags`,
   `abruf = "auf_abruf"`, eine `[[quellen]]` vom Typ `oparl` mit `koerperschaft`, die Themen und
   `[erzeugung]` mit der Zuordnung der OSM-Bezirke.
3. Ortsgliederung erzeugen: `python tools/stadt_aus_osm.py /tmp/streets.updated.csv config/cities/<stadt>.toml`.
4. Auf einem Rechner mit Netz prüfen: `python -m behoerdenmaster --stadt <stadt> probe --quelle ris`,
   danach `pruefstand` in der Konfiguration nachtragen.
5. Diese Übersicht neu erzeugen: `python -m behoerdenmaster staedte --markdown > docs/staedte.md`.
