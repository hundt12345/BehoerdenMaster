# Echtantworten der OParl-Schnittstelle Köln, abgerufen am 11.10.2026

Diese Dateien sind **unveränderte Antworten** von `buergerinfo.stadt-koeln.de/oparl`,
abgerufen am 11.10.2026. Sie dienen ausschließlich dem Test `tests/test_live_oparl_end_to_end.py`,
der den vollständigen Abrufpfad (robots.txt → System → Body → Listen → SQLite → Suche → API)
gegen das reale Schema von heute prüft. Sie sind **kein Datenbestand** und werden von
`demo` nicht geladen.

Lizenz der Originaldaten: Datenlizenz Deutschland – Zero – Version 2.0.

| Datei | Inhalt | Umfang |
|---|---|---|
| `robots.txt` | robots.txt des Hosts, mit CRLF wie im Original | vollständig |
| `system.json` | `/oparl/system` | vollständig |
| `bodies.json` | `/oparl/bodies` | vollständig, `legislativeTerm` auf zwei Einträge gekürzt |
| `organizations.json` | `/oparl/bodies/stadtverwaltung_koeln/organizations?modified_since=2025-11-01T00:00:00+01:00` | **Auszug**: 9 Gremien der ersten Seite plus `gr/1` („Rat“); `gr/193` und `gr/192` wegen langer `membership`-Listen ausgelassen |
| `papers.json` | `/oparl/bodies/stadtverwaltung_koeln/papers?modified_since=2026-10-01T00:00:00+02:00` | **Auszug**: die vier ersten vollständigen Vorlagen der ersten Seite (`vo/135093`, `vo/135089`, `vo/135088`, `vo/135087`); `pagination` und `links` einschließlich `next` unverändert |
| `meetings.json` | `/oparl/bodies/stadtverwaltung_koeln/meetings` | Sitzung `33698` (11. Sitzung des Rates, 08.10.2026) aus [`../sitzungen.json`](../sitzungen.json) |

## Warum dieser Auszug aussagekräftig ist

Die vier Vorlagen verweisen über ihre `consultation` auf dieselbe Sitzung `33698`, die in
`meetings.json` vollständig mit Tagesordnungspunkten vorliegt. Damit ist die Beratungsfolge
geschlossen prüfbar: Vorlage `vo/135088` → `consultations/207194` → `agendaitems/451827` →
überliefertes Ergebnis „ungeändert beschlossen“.

`papers.json` enthält den realen `links.next`-Verweis auf Seite 2. Der Test beantwortet
Seite 2 mit einer leeren Liste, weil die Folgeseiten nicht mit aufgezeichnet wurden; der
Folgelink selbst wird also echt ausgewertet, der Abruf endet dann planmäßig.

## Abgrenzung zu [`../README.md`](../README.md)

Der bisherige Auszug (`vorlagen.json`, Stand der Wahlperiode 2020–2025) blieb unverändert und
wird weiterhin für die Offline-Demo verwendet. Dieser Ordner ist **nicht** Teil der Demo und
nicht Teil des installierten Wheels; er ist Testmaterial mit dem Schema-Stand vom 11.10.2026.
