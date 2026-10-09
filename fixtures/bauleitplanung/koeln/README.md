# Offline-Auszüge: Bauleitplanung Online Köln

**Stand: 9. Oktober 2026. Keine Live-Daten und kein vollständiger Bestand.**

## Herkunft

Öffentliche JSON-Suche der NRW-Plattform, begrenzt auf Köln:

<https://nw.bauleitplanung-online.de/verfahren/suche/ajax?orgaSlug=koeln>

Die recherchierte Antwort enthielt sieben öffentliche Verfahren. `verfahren.json` enthält **drei redaktionell ausgewählte Auszüge**. Das Abrufwerkzeug lieferte die Antwort als dekodierten Text; diese Datei ist **keine unveränderte Rohantwort**. Öffentliche Attribute wurden ausgewählt, Beschreibungen von HTML zu Klartext normalisiert und auf Originalpassagen gekürzt. Die JSON:API-Hülle und Fixture-Metadaten dienen der reproduzierbaren Adapterprüfung. IDs, Titel, Phasenschlüssel, Organisation, Berechtigung und ISO-Zeitstempel wurden aus der öffentlichen Antwort übernommen; keine internen Felder oder Stellungnahmen.

| ID | Verfahren | Beteiligungszeitraum laut API (mit Original-Offset) |
|---|---|---|
| `e42383c2-be4d-4406-8c09-c2d2e9eb5b54` | 255. FNP-Änderung „Neues Quartier Bickendorf sowie Aufhebung einer Fläche für Hauptverkehrszüge“ | `2026-09-25T00:00:00+02:00` – `2026-10-09T02:00:00+02:00` |
| `eb6166b6-af56-4e7f-87e7-0e468dbf7a5b` | „Neues Quartier Bickendorf“ | `2026-09-28T02:00:00+02:00` – `2026-10-12T23:59:59+02:00` |
| `bf1b9a19-a120-446b-99a0-42f1b6e9269c` | 254. FNP-Änderung „Sicherung der Clubkultur im Bereich Lichtstraße / Ehrenfeldgürtel“ | `2026-10-07T23:00:00+02:00` – `2026-11-13T02:00:00+01:00` |

Originalseiten: `https://nw.bauleitplanung-online.de/verfahren/<ID>/public/detail`.

Die genannten ca. 1.600 Wohneinheiten und die voraussichtliche Realisierung ab 2030 sind **Aussagen der öffentlichen Beschreibung**, keine unabhängig geprüften Zusagen. Nicht mit einer Genehmigung oder einem Baubeginn gleichsetzen. Auch die 255. FNP-Änderung und das Quartier bleiben zwei getrennte Quellvorgänge.

## Detail-HTML

`clubkultur-detail.html` ist ein **rekonstruiertes minimales Test-HTML**, kein Mitschnitt und keine vollständige Verfahrensseite. Titel und drei ausgewählte Links wurden am 09.10.2026 auf dieser öffentlichen UUID-Seite geprüft:

<https://nw.bauleitplanung-online.de/verfahren/bf1b9a19-a120-446b-99a0-42f1b6e9269c/public/detail>

- Änderungsbereich: `/file/254fnp-club/aabb27ed-252b-4e0b-a9d7-83e1a6510ed6`
- Planurkunde: `/file/254fnp-club/da116532-6900-4145-a0a3-9b7775b1d88c`
- Bisherige Darstellung: `/file/254fnp-club/c399f6ff-d5e2-4034-8fe1-81a4c0e79fc1`

Dokumenttitel und Größen entsprechen der recherchierten Seite. Der Container-Marker folgt dem öffentlich nachvollziehbaren demosPlan-HTML-Vertrag; die reduzierte Struktur dient nur den Tests. **Die PDFs wurden nicht heruntergeladen.** Die Demo enthält nicht alle Dokumente des Verfahrens; bei den beiden Bickendorf-Verfahren wurden keine Detail-Dokumentlinks in die Demo aufgenommen.

## Nutzung und Grenzen

- Öffentlich zugänglich bedeutet nicht automatisch offen lizenziert. **Keine pauschale offene Datenlizenz verifiziert**; die OParl-Lizenz DL-DE-Zero 2.0 gilt hier nicht automatisch. Nutzungsbedingungen und Rechte an Beschreibungen/Dokumenten beachten.
- Die Demo kennzeichnet ihre eigene Quelle und erzeugt genau eine lokale Erstbeobachtung je Verfahren mit dem oben angegebenen Stand. Das ist **kein amtlicher Verfahrensbeginn**.
- Der echte Crawler übernimmt keine Demo-Historie und keine ungeprüften Demo-Dokumentlinks. Umgekehrt überschreibt die Demo keinen echten Datenbestand.
- Weitere synthetische Fehler-, Login-, Pagination- und Änderungsfälle werden ausschließlich in `tests/test_bauleitplanung.py` erzeugt.
