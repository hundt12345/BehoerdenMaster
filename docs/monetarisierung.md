# Weg in einen bezahlten Betrieb

Stand: 14.10.2026. Dieses Dokument hält fest, was für einen kommerziellen Betrieb **schon
vorbereitet** ist und was noch fehlt. Der Kern (Abruf, Speicherung, Suche, Ortsbezug) bleibt
unverändert; ergänzt wird die „Hülle“ drumherum. Kein Neubau, kein Wechsel des Stacks.

## Grundsätze, die bleiben

- **Suche löst keine Behördenabrufe aus.** Auch zahlende Nutzer erzeugen keine zusätzliche
  Last bei den Behörden; Abrufe folgen Drossel, robots.txt, Budgets und Abkühlzeiten.
- **Namensnennung und Lizenzen** je Quelle bleiben sichtbar (OParl-Lizenzen, ODbL für
  OpenStreetMap). Verkauft wird Aufbereitung, Benachrichtigung und Komfort – nicht die
  amtlichen Daten selbst.
- **SQLite + WAL** auf einer Instanz mit persistenter Disk reicht bis weit in den
  zahlenden Betrieb. Postgres erst, wenn mehrere Instanzen nötig werden.

## Stufe 0 – Betriebsgrundlage (mit diesem Stand vorbereitet)

| Baustein | Stand | Wo |
|---|---|---|
| Mehrere Städte mit demselben System | ✅ Köln dauerhaft; Aachen, Bonn, Düsseldorf, Münster, Wuppertal auf Abruf | `config/cities/`, [`staedte.md`](staedte.md) |
| Laden auf Abruf (Last nur für genutzte Städte) | ✅ Vermerk je Stadt, Verfall, Höchstzahl | `behoerdenmaster/bedarf.py`, `BEHOERDEN_AUF_ABRUF*` |
| Städteübersicht für Nutzer und Vertrieb | ✅ Oberfläche, `GET /api/staedte`, CLI `staedte`, `docs/staedte.md` | `behoerdenmaster/staedte.py` |
| Automatische Sicherungen mit Rotation | ✅ (benötigt persistente Disk) | `BEHOERDEN_BACKUP_*`, `SyncDienst.sicherung` |
| Impressum / Datenschutz | ✅ Seiten vorhanden; Betreiberangaben aus der Umgebung | `behoerdenmaster/rechtliches.py`, `BEHOERDEN_BETREIBER_*` |
| Bezahlte Instanz mit Disk, eigene Domain | ⏳ Betreiberentscheidung | [`render.md`](render.md) |
| Live-Prüfung der neuen Städte | ⏳ je Stadt `probe --quelle ris`, Lizenzangabe übernehmen | TOML-Feld `pruefstand` |
| Monitoring (Uptime auf `/healthz`, Fehlertracking, Alarm bei Abrufpausen) | ⏳ | – |

Vor dem ersten zahlenden Kunden: Impressum/Datenschutz anwaltlich prüfen lassen (die Texte
sind Vorlagen), eine Code-Lizenz festlegen und die Nutzungsbedingungen der Quellen klären.

## Stufe 1 – Konten und Benachrichtigungen (1–2 Wochen)

Der eigentliche zahlungswürdige Mehrwert: „Sag mir, wenn sich in *meinem* Viertel etwas tut.“

- Tabellen `konto` (E-Mail, Magic-Link-Anmeldung, kein Passwort), `watch` (Stadt, Orte/PLZ,
  Themen, Stichworte), `benachrichtigung` (was wann an wen ging).
- Ein Benachrichtigungsjob nach jeder Etappe des `SyncDienst`: neue bzw. geänderte Vorgänge
  gegen die Watches prüfen – rein lokal, ohne zusätzliche Abrufe.
- E-Mail-Versand über einen Dienst wie Postmark oder Resend; tägliche bzw. wöchentliche
  Zusammenfassung, Abmeldelink in jeder Mail.
- Ein Watch auf eine Auf-Abruf-Stadt gilt als dauerhafte Anforderung (`bedarf_melden` bei
  jedem Benachrichtigungslauf) – so bleibt die Stadt aktuell, solange jemand sie abonniert.
- Pilotkunden zunächst manuell abrechnen.

## Stufe 2 – Zahlung und Tarife (2–4 Wochen)

- Zahlungsanbieter als Merchant of Record (Paddle, Lemon Squeezy) oder Stripe mit eigener
  Umsatzsteuerabwicklung; Tabelle `abo` mit Status aus Webhooks.
- Feature-Gates: **Suche bleibt frei.** Bezahlt werden Benachrichtigungen, mehr Watches,
  Exporte, Verlauf/Fristen-Erinnerungen.
- Naheliegende Preisachse: **Städte**. Die Konfiguration je Stadt und das Laden auf Abruf
  machen „Stadt X dauerhaft aktuell halten“ zu einem klar abgrenzbaren Leistungsmerkmal
  (`abruf = "dauerhaft"` für Städte mit zahlenden Kunden).

## Stufe 3 – Geschäftskunden

- White-Label bzw. eigene Instanz für Verbände, Lokalredaktionen, Planungsbüros.
- `/api/v1` mit API-Schlüsseln und Ratenbegrenzung, CSV/JSON-Exporte.
- Postgres und mehrere Instanzen erst bei nachgewiesenem Bedarf.

## Offene Entscheidungen

1. Betreiber (Person/Firma) und damit die Angaben für das Impressum.
2. Hosting-Tarif mit persistenter Disk (Sicherungen und Abrufvermerke überleben Neustarts).
3. Welche Städte zuerst dauerhaft? Kriterium: zahlende Pilotkunden, nicht Größe.
4. Code-Lizenz (derzeit keine festgelegt).
