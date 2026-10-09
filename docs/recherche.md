# Recherche: Datenquellen für BehoerdenMaster

Stand: 9. Oktober 2026. Diese Notizen halten fest, was geprüft wurde, welche Entscheidungen daraus folgen und was offen ist.

## 1. Ratsinformation Köln (OParl) – Hauptquelle

- **Schnittstelle:** `https://buergerinfo.stadt-koeln.de/oparl/system` (OParl 1.1, Hersteller Somacos, E-Mail-Kontakt `12-session@stadt-koeln.de`). Körperschaft `stadtverwaltung_koeln` (AGS 05315000).
- **Listen** (Vorlagen `papers`, Sitzungen `meetings`, Gremien `organizations`) sind paginiert (25 Einträge je Seite). Folgelinks tragen den Filter weiter.
- **IDs stimmen mit der HTML-Ansicht überein:** `papers/vo/101373` entspricht `vo0050.asp?__kvonr=101373`. `meetings/34706` entspricht `si0057.asp?__ksinr=34706`. Dadurch sind Links zur Originalansicht zuverlässig möglich.
- **Beratungsfolge und TOPs** sind eingebettet: Eine Vorlage enthält `consultation` (mit Gremium, Sitzung, TOP und Rolle), eine Sitzung enthält `agendaItem` mit `result`. Damit lässt sich das Verlaufsprotokoll ohne Zusatzabrufe aufbauen.
- **Ortsbezug fehlt (in den geprüften Antworten):** Die erste Seite der Ortsobjekte (`locationList`) enthält nur Sitzungsräume; Straße, PLZ und Stadtteil sind dort leer. In den geprüften Vorlagen fehlt `location`. Stadtteil und Bezirk müssen deshalb aus Betreff und Gremiennamen abgeleitet werden. Beispiele: „Erlenweg in Köln-Bickendorf“, „Bezirksvertretung 4 (Ehrenfeld)“.
- **Dateien:** `File.text` (Volltext) fehlt in den Antworten. PDFs werden nur verlinkt, nicht heruntergeladen.
- **Verfügbarkeit während der Recherche:** Die Seiten `ratsinformation.stadt-koeln.de` und `buergerinfo.stadt-koeln.de` lieferten zeitweise die Meldung „Server ist nicht verfügbar“. Die Recherche wurde daraufhin nicht fortgesetzt, um die Server nicht zusätzlich zu belasten.

### robots.txt (buergerinfo.stadt-koeln.de)

- Keine Gruppe für `User-agent: *`. Unbekannte Clients sind damit nicht eingeschränkt.
- Für namentlich genannte Bots (u. a. Googlebot, Bingbot, GuzzleHttp) gilt `Crawl-delay: 20` und `Disallow: /getfile.asp`. Dieser Wert wird vom Crawler berücksichtigt, falls er für unseren Client gilt.
- Entscheidung: Eigene Drosselung von 3 Sekunden. Sie ist vorsichtiger als das übliche Maß, aber nicht so langsam, dass ein erster Abruf praktisch unmöglich wird.

### Sperren und Erfahrungen anderer Projekte

- **mandari, Issue #89 (08.09.2026):** Seit dem 07.09.2026 waren die Kölner Hosts von der Server-IP des Projekts aus nicht mehr erreichbar. Das Projekt vermutet eine Sperre wegen der Abrufmenge. Wiederholungsversuche nach Fehlern waren nach eigener Darstellung zu schnell.
- **mandari, Issue #348 (23.09.2026, offen):** Abstimmung mit der Stadt Köln über Freigabe, Abrufrate und Zeitfenster. Die Stadt wurde ausdrücklich eingeladen, Vorgaben im Issue festzuhalten. Ein Ausweichen auf andere Adressen wird dort ausgeschlossen.
- **Konsequenzen für BehoerdenMaster:** Konservative Drosselung, Abbruch bei 403/429, wachsende Abkühlzeit, Anfragebudget und Fortsetzung. Vor dem ersten vollständigen Abruf Kontakt zur Stadt aufnehmen.

## 2. Bauleitplanung Online (NRW) – zweite Quelle, noch nicht umgesetzt

- **Adresse:** `https://nw.bauleitplanung-online.de/plaene/koeln`.
- **Inhalt:** Beteiligungsverfahren der Bauleitplanung mit Titel (z. B. „254. Änderung des Flächennutzungsplanes … in Köln-Ehrenfeld“, „Neues Quartier Bickendorf“), Zeitraum, Verfahrensstand und Stadtplanungsamt. Das sind echte Bauvorhaben im Sinne der Frage.
- **robots.txt:** `User-agent: *` ohne Einschränkungen.
- **Technik:** Die Links auf der Übersicht sind platzhalterartig (`#`). Die Liste wird vermutlich clientseitig aufgebaut. Ein Adapter braucht daher entweder die Detailseiten oder die dahinterliegende Schnittstelle. Das muss am Live-System geprüft werden.
- **Vorteil:** Die Plattform wird auch von anderen NRW-Städten genutzt (Muster `…bauleitplanung-online.de/plaene/<stadt>`). Ein Adapter könnte damit mehrere Städte abdecken.

## 3. Bürgerbeteiligung Köln – dritte Quelle, noch nicht umgesetzt

- **Adresse:** `https://meinungfuer.koeln` (Projektübersicht unter `/mitmachen/alle-projekte`). Die Seite listet Projekte mit Zeitraum und Beteiligungsstatus. Die Systematische Öffentlichkeitsbeteiligung der Stadt läuft seit 2019.
- **Stand der Prüfung:** Ob es eine Schnittstelle gibt, ist nicht geklärt. Die Seite wurde nur als Text betrachtet. Eine Abbildung der HTML-Struktur ist daher für den nächsten Schritt vorgesehen.

## 4. PLZ- und Stadtteil-Daten

- **Stadtbezirke und Stadtteile:** 9 Bezirke und 86 Stadtteile (Wikipedia „Liste der Stadtbezirke und Stadtteile Kölns“, Nummern nach der Stadt Köln). Diese Angaben sind in `config/cities/koeln.toml` vollständig übernommen.
- **PLZ → Stadtteil:** Quelle onlinestreet.de („Ortsteile im PLZ-Gebiet“, Datenbasis Destatis/BZSt CC0 und OpenStreetMap ODbL). Für 50823, 50825, 50827, 50829 und 50858 wurde die Zuordnung übernommen. Die Zuordnung ist eine Näherung.
- **Nicht verwendbar:** Wikidata liefert für Köln vor allem Straßen mit PLZ, keine Stadtteil-Zuordnung. Die Stadtteilkarte koelner-stadtteile.de war nicht erreichbar.
- **Offen:** 41 der 46 Kölner PLZ. Die offizielle Quelle wäre die Stadt selbst oder eine amtliche PLZ-Stadtteil-Zuordnung. Im Ergebnis sind PLZ-Suchen daher heute vor allem Treffer mit ausdrücklicher PLZ-Nennung.

## 5. Prior Art

- **mandari** (github.com/mandariOSS/mandari, AGPL-3.0-or-later) bereitet Ratsinformationen für Köln und weitere Städte auf (OParl, SessionNet, ALLRIS). Das Projekt betreibt eine Bürgerschnittstelle „Insight“. Es ist eine sinnvolle Alternative oder ein Partner. Es wurde nur als Recherchequelle genutzt. Es wurde kein Code übernommen. Die Regeln für Abrufe (User-Agent mit Kontakt, robots.txt, höchstens eine Anfrage pro 2 Sekunden, keine Umgehung von Sperren) übernimmt BehoerdenMaster als Grundsatz.

## 6. Offene Fragen an die Stadt Köln

1. Zulässige Abrufrate und Zeitfenster für die OParl-Schnittstelle?
2. Gibt es einen Datenexport oder einen Bulk-Zugang für den Erstabruf?
3. Werden Ortsangaben (Stadtteil, PLZ) künftig an Vorlagen geführt?
4. Gibt es eine Schnittstelle oder einen Datenexport für Bauleitplanung Online und Bürgerbeteiligung?
