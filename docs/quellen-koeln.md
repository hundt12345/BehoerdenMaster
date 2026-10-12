# Weitere Datenquellen der Stadt Köln – Vorschläge

Stand der Recherche: **12. Oktober 2026**. Alle genannten Adressen wurden an diesem Tag
geprüft (Erreichbarkeit, Format, Lizenzangabe). Geprüft heißt nicht abgestimmt: Für
automatische Abrufe gilt weiter Abschnitt [„Regeln für jede neue Quelle“](#regeln-für-jede-neue-quelle).
Das Dokument beschreibt **Vorschläge**, keine Zusagen: Umgesetzt sind derzeit nur
Ratsinformation (OParl) und Bauleitplanung Online NRW.

## Überblick

| # | Quelle | Endpunkt (geprüft) | Format | Lizenz laut Quelle | Aufwand | Nutzen |
|---|---|---|---|---|---|---|
| 1 | SessionNet-RSS des Ratsinformationssystems | `buergerinfo.stadt-koeln.de/rssfeed.asp?id=1&filter=snvf` | RSS/XML | Datenlizenz Deutschland – Zero 2.0 (wie OParl) | **klein** | Sitzungskalender und Vorlagen als schlanker Delta-Kanal |
| 2 | Kölner Bebauungsplan-Übersicht (Aufstellungsbeschlüsse, rechtskräftige Pläne, Fluchtlinien) | `geoportal.stadt-koeln.de/arcgis/rest/services/planen_und_bauen/b_plan_uebersicht/MapServer` | ArcGIS-REST, JSON/GeoJSON | DL-DE-Zero-2.0 (laut Datenkatalog) | mittel | **echter Ortsbezug** + amtlicher Planbestand, den die NRW-Plattform nicht führt |
| 3 | Geoportal-Dienste (Verkehrskalender, Fahrradmaßnahmen, Ladesäulen, Tempo-30-Zonen, Sozialraum) | `geoportal.stadt-koeln.de/arcgis/rest/services/<ordner>/<dienst>/MapServer` | ArcGIS-REST, WFS/WMS | überwiegend DL-DE-Zero-2.0 | mittel | Vorhaben und Maßnahmen räumlich verknüpfen |
| 4 | Öffentliche Bekanntmachungen / Amtsblatt | `stadt-koeln.de/politik-und-verwaltung/bekanntmachungen/oeffentliche-bekanntmachungen-2026` | HTML + PDF | amtliche Bekanntmachung (keine offene Lizenz nötig/keine zugesichert) | klein–mittel | **rechtsverbindliche** Verfahrenschritte (§ 2, § 3 Abs. 2, § 10 BauGB) mit Datum |
| 5 | Offene Daten Köln (DKAN 2) | `www.offenedaten-koeln.de/api/1/…`, Katalog `…/data.json` | JSON, DCAT | DL-DE-Zero-2.0 bzw. DL-DE-BY-2.0 je Datensatz | mittel | Katalog-, Geo- und Statistikdaten mit klarer Lizenz |
| 6 | Einzelne Datensätze: Baustellen Köln, Baumkataster, Aufstellungsbeschlüsse | `geoportal.stadt-koeln.de/wss/service/baustellen_wfs/guest`, `…/baumkataster…` | WFS/WMS, CSV | DL-DE-Zero-2.0 | klein je Datensatz | konkrete, prüfbare Fakten zu einzelnen Verfahren |
| 7 | Beteiligungsportal „Meinung für Köln“ | `meinungfuer.koeln/mitmachen/alle-projekte`, `/archiv/index.html` | HTML (`/jsonapi` = 403) | keine offene Lizenz zugesichert | mittel | Beteiligungsstatus, den RIS und Bauleitplanung nicht zeigen |
| 8 | „Sag's uns“ (Mängelmelder) | `sags-uns.stadt-koeln.de/requests`, `/kategorien` | HTML | keine offene Lizenz zugesichert; **personenbezogene Inhalte möglich** | mittel | Hinweise aus der Stadtgesellschaft – nur aggregiert verarbeiten |
| 9 | Pressemeldungen der Stadt | `www.stadt-koeln.de/externe-dienste/rss/pressemeldungen.xml` | RSS/XML | Pressemitteilung, Zitat mit Quellenangabe | **klein** | Kontext und Vorlauf zu Ratsvorlagen |
| 10 | Statistik (Datenkatalog, Statistikatlas, kleinräumige Zahlen, Straßenverzeichnis) | `stadt-koeln.de/politik-und-verwaltung/statistik/…` | CSV, PDF, ArcGIS | teils DL-DE-Zero-2.0 (Katalog) | klein–mittel | Bezugsgrößen je Stadtteil, Straße → Stadtteil |
| 11 | Wahlen | `wahlen.stadt-koeln.de/prod/<Wahl>/05315000/praesentation/index.html` | HTML/JSON | keine offene Lizenz zugesichert | mittel | Umfeldinformation, keine Kernquelle |

**Empfohlene Reihenfolge:** 1 → 4 → 2 → 5/6 → 7 → 9 → 3 → 8/10/11.
Begründung: Der RSS-Feed und die Bekanntmachungen liefern mit wenig Technik sofort
**belastbare Statuswechsel** zu Vorgängen, die bereits im Bestand sind. Danach folgt mit
der Bebauungsplan-Übersicht der größte inhaltliche Gewinn, weil sie **Geometrie** und den
rechtskräftigen Planbestand mitbringt.

## 1. SessionNet-RSS (Ratsinformationssystem, ergänzend zu OParl)

- **Adresse:** `https://buergerinfo.stadt-koeln.de/rssfeed.asp?id=1&filter=snvf`
  Die Filterbuchstaben sind auf der Seite `buergerinfo.stadt-koeln.de/yhelprss.asp` der Stadt
  dokumentiert: `s` Sitzungstermine, `n` Niederschriften, `v` Vorlagen, `f` zukünftige Termine,
  `a` die letzten 100 Tage. Ohne `filter` wird der Standardumfang ausgegeben.
- **Beobachtung (12.10.2026):** Der Feed listete kommende Sitzungen mit Sitzungsnummer,
  Gremium, Datum, Uhrzeit, Ort und einem Link der Form `rssgo.asp?si34706`. **Die Nummer
  `si34706` ist dieselbe ID, die OParl als `meetings/34706` führt** – der Feed lässt sich
  damit ohne zusätzliche Zuordnungstabelle an bestehende Sitzungen hängen.
- **Aufwand/Nutzen:** gering. Ein Adapter liest ein einzelnes XML-Dokument statt hunderter
  OParl-Seiten und kann zwischen zwei vollen Läufen die „Was ist neu?“-Frage beantworten.
- **Grenzen:** Der Feed ist ein Anreißer, keine vollständige Datenquelle (keine Dokumente,
  keine Beratungsfolge). Er **ersetzt den OParl-Abruf nicht** und darf nicht als Beleg für
  Vollständigkeit ausgegeben werden.
- **Umsetzung:** `typ = "oparl-rss"`; Ergebnis nur als „Sitzungstermin/Hinweis“ speichern,
  Upsert über die Sitzungsnummer; keine Löschung bestehender OParl-Daten.

## 2. Kölner Bebauungsplan-Übersicht (ArcGIS REST)

- **Dienst:** `https://geoportal.stadt-koeln.de/arcgis/rest/services/planen_und_bauen/b_plan_uebersicht/MapServer`
  Layer: `0` Fluchtlinienpläne, `1` B-Pläne rechtskräftig, `2` Flurstücke, `3` Aufstellungsbeschlüsse.
- **Layer 3 (Aufstellungsbeschlüsse), geprüfte Felder:** `arbeitstitel`, `planungsziel` (bis 60.000
  Zeichen), `bekanntmachung_aufstellungsbeschluss` (Datum), `telefon_nr`, `link`. Der Dienst
  unterstützt `Query` und `Data`, GeoJSON-Ausgabe, `maxRecordCount 5000`.
- **Zuschnitt:** Der Dienstbeschreibung zufolge sind es die **aktuellen Aufstellungsbeschlüsse
  des Stadtplanungsamtes**; das förmliche Verfahren beginnt mit dem Beschluss des
  Stadtentwicklungsausschusses und der Bekanntmachung im Amtsblatt.
- **Warum zusätzlich zur bereits eingebundenen NRW-Plattform?** Bauleitplanung Online zeigt
  Beteiligungsverfahren; hier stehen **Aufstellungsbeschluss mit Bekanntmachungsdatum** und
  **rechtskräftige Pläne** samt Geltungsbereich-Geometrie. Ein Planverfahren lässt sich damit
  über die Geometrie einem Stadtteil zuordnen, statt den Ortsnamen zu raten.
- **Verwandte Dienste im selben Ordner:** `flaechennutzungsplan`, `veraenderungssperre`,
  `baurechtliche_ortssatzungen`, `stellplatzsatzung`, `bauprojekte_altstadtkern`,
  `baulandumlegungen`.
- **Vorsicht:** Die Plan-Festsetzungen sind laut Dienstbeschreibung ausdrücklich nicht immer
  aktuell und vollständig; für Aussagen zur Rechtsverbindlichkeit ist die Bebauungsplan-Übersicht
  bzw. das Planoriginal maßgeblich. Diese Hinweise gehören in die Oberfläche, nicht ins Kleingedruckte.

## 3. Geoportal-Dienste (`geoportal.stadt-koeln.de/arcgis/rest/services`)

Geprüfte Ordner (12.10.2026): `basiskarten`, `bildung_und_schule`, `druckservices`,
`familie_partnerschaft_kinder`, `freizeit_natur_sport`, `gesellschaft_soziales_wohnen`,
`gesichert`, `klima_umwelt_tiere`, `kultur`, `luftbilder`, `planen_und_bauen`,
`politik_und_verwaltung`, `statistische_daten`, `suchdienste`, `Utilities`, `verkehr`.

Besonders anschlussfähig an die Suche nach kommunalen Vorgängen:

| Dienst | Inhalt | Nutzen für BehoerdenMaster |
|---|---|---|
| `verkehr/verkehrskalender` | Layer `Standort`, `Strecke`, `Bereich`, `Verkehrslage`, Parken/P+R | Verkehrsmaßnahmen räumlich aufsuchen (Map/Query/Data) |
| `verkehr/fahrradverkehr_massnahmen` | geplante/umgesetzte Radverkehrsmaßnahmen | passt zu Ratsvorlagen „Radweg/Fahrradstraße“ |
| `verkehr/zone_tempo30`, `verkehr/e_ladesaeulen`, `verkehr/kvb_haltestellen` | Infrastruktur | Umfeld einer Beschlussvorlage |
| `politik_und_verwaltung/sozialraum` | Sozialraumdaten | Kontext zu Quartiersplanung |
| `politik_und_verwaltung/gebaeudewirtschaft` | Bauprojekte städtischer Gebäude | Gegenstück zu Bauleitverfahren |
| `politik_und_verwaltung/soziale_erhaltungssatzung` | Gebiete mit Erhaltungssatzung | § 172 BauGB – verdrängungsrelevanter Status |
| `statistische_daten/statistikdaten`, `qmfs_*` | kleinräumige Statistik | Kennzahlen je Stadtteil/Quartier |

- **Formate:** ArcGIS-REST (`?f=geojson`, `…/query?where=…`), zusätzlich WFS/WMS unter
  `geoportal.stadt-koeln.de/wss/service/<dienst>/guest`.
- **Lizenz:** In den offenen Datensätzen ist für Geodaten überwiegend **DL-DE-Zero-2.0**
  angegeben. Sie gilt pro Datensatz, nicht pauschal für das gesamte Geoportal.
- **Aufwand:** Ein Adapter für ArcGIS-REST erschließt mehrere Dienste; Geometrien lassen sich
  über Punkt-in-Polygon-Verfahren mit den vorhandenen Stadtteil-/Bezirksdaten verbinden.

## 4. Öffentliche Bekanntmachungen und Amtsblatt

- **Übersicht:** `https://www.stadt-koeln.de/politik-und-verwaltung/bekanntmachungen/index.html`
  mit der Jahresliste `…/bekanntmachungen/oeffentliche-bekanntmachungen-2026` (Satzungen,
  Bauleitpläne, Allgemeinverfügungen). Die Liste lässt sich über `?letter=A`…`?letter=Z`
  und eine Suchfunktion einschränken.
- **Aufbau der Liste (12.10.2026 geprüft):** Jeder Eintrag nennt ein Datum, die Art der
  Bekanntmachung, einen **Arbeitstitel** und einen PDF-Link sowie ein „Hinweisveröffentlichung“-
  Datum. Beispiele aus dem geprüften Bestand:
  - 07.10.2026 – „Frühzeitige Beteiligung der Öffentlichkeit an einem Bebauungsplan-Entwurf,
    Arbeitstitel: ‚Feldgärtenstraße in Köln-Niehl‘“
  - 07.10.2026 – „Inkrafttreten eines Bebauungsplans gemäß § 10 BauGB, Arbeitstitel:
    ‚Volkhovener Straße in Köln-Esch/Auweiler‘“
  - 30.09.2026 – „Veröffentlichung eines Flächennutzungsplan-Entwurfs, 254. Änderung,
    Sicherung der Clubkultur im Bereich Lichtstraße/Ehrenfeldgürtel“
  - 30.09.2026 – „Veröffentlichung eines Flächennutzungsplan-Entwurfs, 246. Änderung,
    Städtebauliche Entwicklung Longericher Straße in Köln-Bilderstöckchen“
- **Die Typbezeichnung ist der Status.** Formulierungen wie „Frühzeitige Beteiligung …“,
  „Veröffentlichung eines Flächennutzungsplan-Entwurfs“, „Inkrafttreten gemäß § 10 BauGB“
  entsprechen den Verfahrensschritten nach BauGB. Ein Parser kann daraus einen belastbaren
  Status ableiten – anders als aus Prosa-Kurzbeschreibungen.
- **Dokumentpfade:** einzelne Bekanntmachungen unter
  `…/mediaasset/content/bekanntmachungen/<jahr>/<JJJJ.MM.TT>_<NNNN-NN>_<kurzname>.pdf`,
  z. B. `…/bekanntmachungen/2026/2026.10.07_0190-01_satzung_volkhovener-str.pdf`.
  Das Amtsblatt liegt unter `…/mediaasset/content/pdf13/amtsblatt-<jahr>/amtsblatt_<nr>_<datum>.pdf`.
- **Nutzen:** Das sind die **amtlichen Statuswechsel** (Aufstellung § 2 Abs. 1, Offenlage
  § 3 Abs. 2, Satzungsbeschluss/Inkrafttreten § 10). Bauleitplanung Online und die RIS-Vorlage
  zeigen den politischen Weg; hier steht, was rechtlich in Kraft ist.
- **Grenzen:** Die Bekanntmachung ist ein PDF. Wir würden **Metadaten und Link** übernehmen
  (Art, Datum, Arbeitstitel, PDF-Link), nicht den Fließtext der PDF-Konvertierung. Ein
  PDF-Volltextabruf ist eine eigene, separat zu bewertende Entscheidung (siehe Regeln). Auch
  die hier verlinkten Plannummern sind nur dann stabil, wenn die Stadt sie beibehält – siehe
  offene Fragen 3 und 4.

## 5./6. Offene Daten Köln (DKAN 2) und einzelne Datensätze

Das Portal wurde 2026 auf DKAN 2 umgestellt; die API-Dokumentation beschreibt es in
`offenedaten-koeln.de/node/44162`. Geprüfte Aufrufe:

```text
Katalogsuche        https://www.offenedaten-koeln.de/api/1/search/?fulltext=Bebauungsplan&page-size=25
Metadaten (Liste)   https://www.offenedaten-koeln.de/api/1/metastore/schemas/dataset/items
Metadaten (einzeln) https://www.offenedaten-koeln.de/api/1/metastore/schemas/dataset/items/<uuid>
Daten (Datastore)   https://www.offenedaten-koeln.de/api/1/datastore/query/<uuid>/<index>?limit=100&offset=0
Gesamtkatalog DCAT  https://www.offenedaten-koeln.de/data.json
```

- **Antwortformat:** JSON (DCAT-Beschreibung bzw. Tabellenzeilen), Datastore unterstützt
  `conditions`, `sorts`, `limit`, `offset` und `?download`.
- **Lizenz:** Die Metadaten nennen je Verteilung u. a. `dl-zero-de/2.0` und `dl-by-de/2.0`.
  **BY-Datensätze brauchen eine Namensnennung** – das muss je Datensatz ausgelesen und
  angezeigt werden, nicht global angenommen.
- **Für Uns relevant, geprüft:** „Bebauungspläne Köln Bauungspläne“ (ArcGIS-JSON + WMS),
  „Bebauungspläne Köln Aufstellungsbeschlüsse“, „Baustellen Köln“ (genehmigte Baumaßnahmen mit
  Auswirkung auf das Straßenland, Amt für Verkehrsmanagement, WFS `baustellen_wfs/guest` plus
  WMS), „Baumkataster Köln“, „Statistischer Datenkatalog Köln“.
- **Aufwand:** neuer Quelltyp `opendata` (Katalog lesen, Datensatz-ID in der Stadtkonfiguration
  hinterlegen, Zeilen in das gemeinsame Modell abbilden). Der Regelbetrieb ist gutmütig, weil
  der Katalog selten wechselt und große Datenmengen einmalig geladen werden können.
- **Caveat:** Ein Teil der Datensätze verweist auf **andere Herausgeber** (z. B. Landesdatenbank
  NRW). Deren Nutzungsbedingungen gelten dann zusätzlich.

## 7. Beteiligungsportal „Meinung für Köln“

- **Adresse:** `https://meinungfuer.koeln/mitmachen/alle-projekte` (Projekte seit Juli 2022),
  Archiv unter `https://meinungfuer.koeln/archiv/index.html`.
- **Beobachtung:** Jede Projektkarte nennt Titel, Träger, Kurzbeschreibung, Statuszeile
  („Online-Beteiligung seit 10 Tagen geschlossen“) und einen Detail-Link.
- **Keine offene Schnittstelle:** `https://meinungfuer.koeln/jsonapi` antwortet mit
  **403** („The 'access jsonapi routes' permission is required.“). Ein RSS-Feed wurde nicht
  geprüft; er wäre der erste Kandidat vor einem HTML-Parser.
- **Nutzen:** Ergänzt die Bauleitbeteiligung um Vorhaben, die (noch) kein Bauleitverfahren
  haben – im geprüften Bestand z. B. GRÜNZUG Flittard-Stammheim, Qualifizierungsverfahren
  Neubrücker Ring, Suchthilfezentrum am Perlengraben, Hybridpark.
- **Aufwand:** mittel (HTML-Struktur, Statuszeilen sind Prosa und müssen in ein eigenes Feld,
  nicht in „Phase“). Nur die Liste plus Detailseite lesen, keine Kommentare und keine Beiträge
  Dritter – dort stehen personenbezogene Meinungen.

## 8. „Sag's uns“ (Mängelmelder)

- **Adresse:** `https://sags-uns.stadt-koeln.de/` mit `/requests`, `/kategorien`, `/report`.
  Die Stadt weist selbst darauf hin: **„Bitte beachten Sie, dass jede Meldung öffentlich
  einsehbar ist.“**
- **Bewertung:** Interessant als Stimmungsbild je Stadtteil, aber ohne zugesicherte offene
  Lizenz und mit Standortdaten. Wenn überhaupt, nur **aggregiert je Stadtteil und Kategorie**
  übernehmen, niemals Fotos, Namen oder Meldertexte. Erst robots.txt und Nutzungsbedingungen
  prüfen; ein Abruf der Einzelmeldungen in einem Takt ist nicht vorgesehen.

## 9. Pressemeldungen der Stadt (RSS)

- **Adresse:** `https://www.stadt-koeln.de/externe-dienste/rss/pressemeldungen.xml`
- **Beobachtung (12.10.2026):** aktueller Feed mit etwa einer Meldung pro Tag und Titel, Link,
  Kurztext und Datum, z. B. „Bebauungsplanverfahren Feldgärtenstraße in Köln-Niehl“ (07.10.2026),
  „Stadt Köln sorgt für kompakte und lebenswerte Quartiere“ (09.10.2026).
- **Nutzen:** Ein vorbereiteter Kanal, um zu einem Verfahren die **Pressemitteilung der Stadt**
  als Hintergrund und Einladung zur Beteiligung anzuzeigen. Das ist Öffentlichkeitsarbeit der
  Stadt selber, nicht die eines Mediums.
- **Umsetzung:** `typ = "presse-rss"`; nur Titel, Datum, Link, Kurztext speichern und über
  Orts-/Themenbezug an bestehende Vorgänge hängen. Keine Volltexte Dritter.

## 10. Statistik, 11. Wahlen

- **Statistik:** `stadt-koeln.de/politik-und-verwaltung/statistik/…` (Datenkatalog als offener
  Datensatz, Statistikatlas, kleinräumige Statistiken, Kölner Monatszahlen, Statistische
  Jahrbücher, Straßenverzeichnisse). Nutzen: (a) Kennzahlen je Stadtteil als Kontext,
  (b) das **Straßenverzeichnis** als mögliche Grundlage, um Straßennamen aus Vorlagentexten
  einem Stadtteil zuzuordnen – das wäre ein Qualitätssprung für den Ortsbezug.
- **Wahlen:** `wahlen.stadt-koeln.de` (z. B. Bürgerentscheid 2026 mit Ergebnisdarstellung).
  Eigene Domäne, eigenes Datenmodell. Als Quelle reizvoll, aber außerhalb des Themas
  „kommunale Vorgänge“ – erst nach den Kernthemen angehen.

## Regeln für jede neue Quelle

Für jede neue Quelle gilt, was schon für OParl und Bauleitplanung gilt – ohne Ausnahme:

1. **Erkennbarer Abrufer** mit Kontaktadresse im User-Agent.
2. **robots.txt zuerst**, inklusive `Crawl-delay`; keine Umgehung von Sperren oder Anmeldungen.
3. **Mindestabstand** (Standard 3 Sekunden, je Quelle konfigurierbar) und ein **Anfragebudget**
   je Lauf.
4. **Lizenz je Quelle prüfen und anzeigen.** DL-DE-Zero und DL-DE-BY sind nicht dasselbe;
   für Bauleitplanung Online, meinungfuer.koeln, „Sag's uns“, Wahlen und Bekanntmachungen ist
   **keine** pauschale offene Lizenz zugesichert.
5. **Keine PDF-Downloads und kein PDF-Volltext**, solange das nicht ausdrücklich entschieden
   und mit dem Betreiber abgestimmt ist. Dokumente bleiben verlinkt.
6. **Keine personenbezogenen Inhalte** (Kommentare, Meldungen, Namen, Fotos) übernehmen.
7. **Quelle sichtbar machen:** Jedes Ergebnisobjekt trägt Quelle, Herkunft und Lizenz; keine
   Quelle erbt die Links oder die Lizenz einer anderen (kein OParl-Link an einem Bauleitobjekt).
8. **Adressen mit der Stadt abstimmen**, bevor ein vollständiger Abruf läuft – die offenen
   Fragen stehen in [recherche.md](recherche.md#6-offene-fragen-an-die-stadt-köln).

## Offene Fragen an die Stadt Köln (ergänzt)

1. Ist der SessionNet-RSS-Feed (`rssfeed.asp`) für einen automatischen Abruf im beschriebenen
   Umfang gedacht? Welcher Abrufabstand ist vorgesehen?
2. Sind die ArcGIS-Dienste des Geoportals (Bebauungsplan-Übersicht, Verkehrskalender,
   Fahrradmaßnahmen) als Dauerangebot mit der Lizenz DL-DE-Zero-2.0 für Weiterverbreitung
   nutzbar – auch die Geometrien?
3. Gibt es für die **öffentlichen Bekanntmachungen** einen strukturierten Zugang (JSON/CSV)
   statt PDF, etwa mit Plannummer und Verfahrensschritt?
4. Führt die Stadt für das Bebauungsplanverfahren eine **stabile Verfahrens-ID**, die sich
   zwischen Bauleitplanung Online, Amtsblatt, SessionNet und Bebauungsplan-Übersicht
   wiederfindet? Ohne sie bleibt die Verknüpfung eine Heuristik.
5. Ist ein Export oder eine Schnittstelle für das Beteiligungsportal geplant (RSS wäre ein Anfang)?
6. Gilt für „Sag's uns“ eine Weiterverwendung, und dürfen Meldungen **aggregiert** ausgewertet werden?

## Was hier bewusst fehlt

- **Volltext von Zeitungsartikeln:** Presseartikel (z. B. Kölner Stadt-Anzeiger, Rundschau,
  Express) werden **nicht** abgerufen oder gespeichert. Sie sind urheberrechtlich geschützt und
  stehen teils hinter Bezahlschranken. Zulässig und nützlich ist der Weg über **eigene Texte
  mit Quellenangabe** – siehe [presse-koeln.md](presse-koeln.md).
- **Umgehung von Sperren:** Hosts, die den Abruf verweigern (siehe mandari-Issues), bleiben
  verweigert. Keine Ausweichadressen, keine Proxys, keine schnelleren Wiederholungen.
- **Fremde Kommunen:** Weitere Städte auf derselben demosPlan- oder OParl-Technik können später
  über `config/cities/<stadt>.toml` folgen; Kandidaten mit öffentlichem OParl finden sich im
  OParl-Verzeichnis. Das ist ein eigener Arbeitsschritt.
