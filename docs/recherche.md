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

## 2. Bauleitplanung Online (NRW) – zweite Quelle, implementiert

- **Übersicht:** `https://nw.bauleitplanung-online.de/plaene/koeln`. Die reine Textansicht zeigte nur die Überschrift; deshalb nicht als Scraping-Vertrag verwendet.
- **Öffentliche Schnittstelle verifiziert:** `https://nw.bauleitplanung-online.de/verfahren/suche/ajax?orgaSlug=koeln`. Die Antwort vom 09.10.2026 enthielt sieben `Procedure`-Objekte in einer JSON:API-artigen Hülle (`data`, `included`, `links`, `jsonapi`, `meta`). Kein Folgelink in der beobachteten Antwort; Pagination wird vorsorglich unterstützt und offline getestet.
- **Öffentliche Attribute:** `externalName`, `externalDescription`, `externalStartDate`, `externalEndDate`, `externalPhaseTranslationKey`, `externalPhasePermissionset`, `owningOrganisationName`. Organisation: `Stadt Köln - 61 Stadtplanungsamt ` (mit Leerzeichen am Ende). Das aktuelle Upstream-Schema verwendet teils `externalPhaseDefinitionName`; beide Varianten werden unterstützt, ohne interne Namen als Ersatz heranzuziehen.
- **Scope:** `orgaSlug=koeln` bleibt auch auf Folgeseiten erhalten. Zusätzlicher Abgleich der Organisation, keine hidden/undefinierte Berechtigungssicht. Unbekannte oder kaputte Antwort-Schemata werden nicht als erfolgreich leere Liste behandelt.
- **Originalseite über UUID:** `https://nw.bauleitplanung-online.de/verfahren/<UUID>/public/detail`. Das Verfahren `bf1b9a19-a120-446b-99a0-42f1b6e9269c` (254. FNP „Sicherung der Clubkultur …“ in Ehrenfeld) war öffentlich lesbar, mit Phase Öffentlichkeit und dem Zeitraum 07.10.–13.11.2026.
- **Dokumentlinks geprüft:** u. a. `/file/254fnp-club/aabb27ed-252b-4e0b-a9d7-83e1a6510ed6` (Änderungsbereich), `/file/254fnp-club/da116532-6900-4145-a0a3-9b7775b1d88c` (Planurkunde), `/file/254fnp-club/c399f6ff-d5e2-4034-8fe1-81a4c0e79fc1` (bisherige Darstellung). Nur Links derselben Origin mit öffentlichem `/file/<…>/<…>`-Muster; keine PDF-Downloads.
- **Alte Slugs nicht voraussetzen:** Der recherchierte frühere `maxbecker-areal`-Link leitete zur Anmeldung weiter. Er wird nicht als aktuelle öffentliche Quelle verwendet und die Anmeldung nicht umgangen.
- **robots.txt:** `User-agent: *` ohne Einschränkungen. Eigener Mindestabstand von 3 Sekunden, Kontaktkennung, Budget und gemeinsamer Cooldown gelten dennoch.
- **Kein geprüfter modified-since-Filter:** Nach einem abgeschlossenen Zyklus die ganze öffentliche Liste erneut lesen. Nach Budgetabbruch persistente Listen-/Detail-Fortsetzung, ohne die fertige Liste erneut zu laden. Seiten und Cursor atomar speichern; eindeutige Zyklusmarker unterscheiden auch zwei Läufe innerhalb einer Sekunde.
- **Verlauf:** Beteiligungsdaten aus der Quelle und lokale Inhalts-Snapshots bei Titel-/Phasen-/Frist-/Beschreibung-/Organisations-/Berechtigungsänderung. Abrufdatum nicht als amtliches Änderungsdatum ausgeben. Nicht mehr gelistet heißt weder beschlossen noch fertig gebaut; alte Verfahren und zuletzt bekannte Dokumentlinks bleiben gespeichert.
- **Geografie:** Titel und öffentliche Beschreibung liefern heuristische Ortsangaben. Keine Flächen-/Geometrie-Auswertung. Kontaktangaben der Detailseite nicht als Lage des Projekts übernehmen.
- **Lizenz:** Öffentlich zugänglich, aber keine pauschale offene Datenlizenz verifiziert. DL-DE-Zero der OParl-Daten gilt hier nicht automatisch; Nutzungsbedingungen und Dokumentrechte gesondert klären.
- **Verifikation begrenzen:** Öffentliche Antwort/Detailseite per Recherchewerkzeug gelesen, End-to-End-Adapter gegen Mock-HTTP geprüft. Ein direkter Live-Crawl aus dieser Sandbox wurde **nicht erfolgreich bestätigt**. Die Fixture ist ein normalisierter redaktioneller Auszug, keine Rohantwort; siehe `fixtures/bauleitplanung/koeln/README.md`.

### Beobachtete öffentliche Verfahren (09.10.2026)

| UUID | Titel (gekürzt) | Phase / Zeitraum laut API |
|---|---|---|
| `e42383c2-be4d-4406-8c09-c2d2e9eb5b54` | 255. FNP „Neues Quartier Bickendorf / Aufhebung Hauptverkehrszüge“ | frühzeitig, 25.09.–09.10.2026 (Endzeit 02:00 +02:00) |
| `aa3eabea-b186-4c1e-80b0-bdad8d018298` | Hansaring / Turiner Straße | frühzeitig, 16.09.–09.10.2026 |
| `eb6166b6-af56-4e7f-87e7-0e468dbf7a5b` | Neues Quartier Bickendorf | frühzeitig, 28.09.–12.10.2026 |
| `c7184370-7e4c-4ca4-ac4b-4cdb42353064` | Käulchensweg, Poll, 1. Änderung / Teilaufhebung | Öffentlichkeit, 17.09.–19.10.2026 |
| `dedf1ecc-a282-49ec-9e99-d38c8d8a2b83` | Feldgärtenstraße, Niehl | frühzeitig, 07.10.–26.10.2026 |
| `bd1110c9-b5d4-4170-be3c-0254f88bfec6` | 246. FNP Longericher Straße, Bilderstöckchen | Öffentlichkeit, 07.10.–13.11.2026 |
| `bf1b9a19-a120-446b-99a0-42f1b6e9269c` | 254. FNP Sicherung der Clubkultur, Ehrenfeld | Öffentlichkeit, 07.10.–13.11.2026 |

Die Tabelle ist eine damalige Beobachtung, **kein vollständiges Archiv und keine Aussage über heutige Verfügbarkeit**. Vollständige ISO-Zeitstempel für die drei Demo-Verfahren stehen in den Fixtures. Auffällige Uhrzeiten wurden nicht redaktionell auf „Ende des Tages“ geändert.

### demosPlan-Protokollrecherche

Öffentliche Implementierung: `https://github.com/demos-europe/demosplan-core`, recherchierter Stand `6426ce3`, Lizenz EUPL-1.2. Relevante Dateien:

- `DemosPlanProcedureAPIController.php`: öffentliche Suchroute `/verfahren/suche/ajax`, Query-Parameter `orgaSlug`.
- `PublicIndexProcedureLister.php`: öffentliche Filterung und kommunale Organisationsauswahl.
- `ProcedureResourceType.php`: öffentliche/extern sichtbare Felder und Phasenbezeichnungen.
- Public-detail-Templates: Marker `infolistContentMetaDescription` bzw. `helpPublicDetailTimelimit`, Dokumentlinks.

Es wurde **kein Anbieter-Code übernommen**; Adapter und HTMLParser sind eigene Implementierungen. Eine alternative Route `/api/werdenktwas/procedures` existiert im Upstream, wurde aber weder als Live-Quelle geprüft noch eingebunden. Andere demosPlan-Installationen können andere Versionen/Markup verwenden und benötigen eine eigene Kompatibilitätsprüfung.

## 3. Bürgerbeteiligung Köln – dritte Quelle, noch nicht umgesetzt

- **Adresse:** `https://meinungfuer.koeln` (Projektübersicht unter `/mitmachen/alle-projekte`). Die Seite listet Projekte mit Zeitraum und Beteiligungsstatus. Die Systematische Öffentlichkeitsbeteiligung der Stadt läuft seit 2019.
- **Stand der Prüfung:** Ob es eine Schnittstelle gibt, ist nicht geklärt. Die Seite wurde nur als Text betrachtet. Eine Abbildung der HTML-Struktur ist daher für den nächsten Schritt vorgesehen.

## 4. PLZ- und Stadtteil-Daten

- **Stadtbezirke und Stadtteile:** 9 Bezirke und 86 Stadtteile (Wikipedia „Liste der Stadtbezirke und Stadtteile Kölns“, Nummern nach der Stadt Köln). Diese Angaben sind in `config/cities/koeln.toml` vollständig übernommen.
- **PLZ → Stadtteil:** Quelle onlinestreet.de („Ortsteile im PLZ-Gebiet“, Datenbasis Destatis/BZSt CC0 und OpenStreetMap ODbL). Für 50823, 50825, 50827, 50829 und 50858 wurde die Zuordnung übernommen. Die Zuordnung ist eine Näherung.
- **Nicht verwendbar:** Wikidata liefert für Köln vor allem Straßen mit PLZ, keine Stadtteil-Zuordnung. Die Stadtteilkarte koelner-stadtteile.de war nicht erreichbar.
- **Offen:** 41 der 46 Kölner PLZ. Die offizielle Quelle wäre die Stadt selbst oder eine amtliche PLZ-Stadtteil-Zuordnung. Im Ergebnis sind PLZ-Suchen daher heute vor allem Treffer mit ausdrücklicher PLZ-Nennung.

## 5. Prior Art

- **demosPlan** (github.com/demos-europe/demosplan-core, EUPL-1.2) wurde zur öffentlichen Protokoll-/Markup-Recherche genutzt, siehe Abschnitt 2; keine Codeübernahme.
- **mandari** (github.com/mandariOSS/mandari, AGPL-3.0-or-later) bereitet Ratsinformationen für Köln und weitere Städte auf (OParl, SessionNet, ALLRIS). Das Projekt betreibt eine Bürgerschnittstelle „Insight“. Es ist eine sinnvolle Alternative oder ein Partner. Es wurde nur als Recherchequelle genutzt. Es wurde kein Code übernommen. Die Regeln für Abrufe (User-Agent mit Kontakt, robots.txt, höchstens eine Anfrage pro 2 Sekunden, keine Umgehung von Sperren) übernimmt BehoerdenMaster als Grundsatz.

## 6. Offene Fragen an die Stadt Köln

1. Zulässige Abrufrate und Zeitfenster für die OParl-Schnittstelle?
2. Gibt es einen Datenexport oder einen Bulk-Zugang für den Erstabruf?
3. Werden Ortsangaben (Stadtteil, PLZ) künftig an Vorlagen geführt?
4. Ist die öffentliche Bauleit-Suche offiziell für solche Abrufe unterstützt; welche Abrufrate und Nutzungsbedingungen gelten?
5. Gibt es einen unterstützten Export oder eine API für Bürgerbeteiligung (`meinungfuer.koeln`)?
